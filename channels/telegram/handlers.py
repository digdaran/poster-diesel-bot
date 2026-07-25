"""Обработчики диалога Telegram-бота (п.8.1, 10.2-10.5 ТЗ).

Вся бизнес-логика — вызовы app/services/*; здесь только приём событий и
отрисовка ответов средствами aiogram. Канал создаётся один раз в main.py и
передаётся сюда через `set_channel()` (простая замена DI-контейнера для
единственного процесса канала).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from app.core.phone import InvalidPhoneError
from app.models.enums import ChannelType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.ticket import Ticket
from app.services import participant_service, settings_service
from app.services import payment_service as payment_svc
from sqlalchemy import select
from sqlalchemy.orm import Session

from channels.telegram.state import (
    QUANTITY_OPTIONS,
    PurchaseStates,
    RegistrationStates,
    get_active_provider,
    get_channel_db,
    get_provider_for_type,
)

if TYPE_CHECKING:
    from channels.telegram.channel import TelegramChannel

logger = structlog.get_logger(__name__)
router = Router(name="telegram-main")

_MAIN_KEYBOARD_BUTTONS = [["🖼 Купить постер", "📋 Мои покупки"], ["ℹ️ Помощь"]]

_channel: TelegramChannel | None = None  # устанавливается через set_channel() в main.py


def set_channel(channel: TelegramChannel) -> None:
    global _channel
    _channel = channel


def _get_channel() -> TelegramChannel:
    if _channel is None:
        raise RuntimeError("TelegramChannel не инициализирован — вызовите set_channel() в main.py")
    return _channel


def _uid(entity: Message | CallbackQuery) -> str:
    """ID пользователя Telegram. `from_user` отсутствует только для событий
    каналов/анонимных админов, которых этот бот не обрабатывает."""
    assert entity.from_user is not None, "Ожидалось событие от пользователя (from_user)"
    return str(entity.from_user.id)


def _msg(callback: CallbackQuery) -> Message:
    from aiogram.types import Message as _Message

    assert isinstance(callback.message, _Message), "Сообщение недоступно для редактирования/ответа"
    return callback.message


@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext) -> None:
    """Главная клавиатура (покупка/история/помощь) открывается только после
    ПОЛНОЙ регистрации — подтверждённый номер И указанное имя. До этого
    момента доступна только клавиатура текущего шага регистрации."""
    await state.clear()
    channel = _get_channel()
    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id=_uid(message)
        )
        ignore_verification = settings_service.get_or_create_settings(
            session
        ).ignore_phone_verification

    if participant is None:
        await message.answer("Добро пожаловать в бот продажи цифровых постеров!")
        await channel.request_contact(_uid(message))
        if ignore_verification:
            # П.7.1 ТЗ: при включённом флаге ручной ввод номера тоже открывает
            # доступ, поэтому кнопка «Поделиться контактом» — не единственный путь.
            await message.answer("Либо просто напишите номер телефона в чат.")
            await state.set_state(RegistrationStates.awaiting_phone)
        return

    if participant.full_name is None:
        # Номер подтверждён ещё до появления сбора имени — доспрашиваем при
        # следующем /start, а не только сразу после on_contact.
        await message.answer("Добро пожаловать в бот продажи цифровых постеров!")
        await message.answer("Как вас зовут?")
        await state.set_state(RegistrationStates.awaiting_name)
        return

    keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
    await message.answer("Добро пожаловать в бот продажи цифровых постеров!", reply_markup=keyboard)


@router.message(F.contact)
async def on_contact(message: Message, state: FSMContext) -> None:
    """Подтверждение номера через «Поделиться контактом» (п.7.1, 10.2 ТЗ)."""
    contact = message.contact
    if contact is None or (
        message.from_user is not None and contact.user_id != message.from_user.id
    ):
        # Пересланный чужой контакт не принимается — подтверждение только для себя.
        await message.answer("Пожалуйста, поделитесь именно своим контактом.")
        return

    db = get_channel_db()
    with db.session() as session:
        result = participant_service.confirm_channel_binding(
            session,
            channel=ChannelType.TELEGRAM,
            external_user_id=_uid(message),
            phone_raw=contact.phone_number,
            username=message.from_user.username if message.from_user else None,
        )
        if result.conflict:
            logger.warning(
                "channel_rebind_conflict",
                external_user_id=_uid(message),
                phone=contact.phone_number,
            )
            await message.answer(
                "Этот телеграм-аккаунт уже привязан к другому номеру. Обратитесь к оператору."
            )
            return
        has_name = bool(result.participant.full_name)

    if has_name:
        channel = _get_channel()
        keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
        await message.answer(
            "Номер принят! Теперь вам доступна история покупок.",
            reply_markup=keyboard,
        )
        return

    await message.answer("Номер принят! Как вас зовут?")
    await state.set_state(RegistrationStates.awaiting_name)


@router.message(F.photo | F.document)
async def on_receipt_upload(message: Message) -> None:
    """Квитанция об оплате, присланная участником — привязывается к его текущему
    неоплаченному счёту (`Payment.status == PENDING`), сохраняется как есть, БЕЗ
    распознавания (см. ТЗ, DECISIONS.md). Не привязана к FSM-состоянию — участник
    может прислать квитанцию в любой момент после получения QR, поэтому
    зарегистрирован рано (как `on_contact`), чтобы не оказаться в тени
    состояний диалога регистрации/покупки."""
    from app.models.payment import Payment

    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id=_uid(message)
        )
        payment = None
        if participant is not None:
            payment = (
                session.execute(
                    select(Payment)
                    .where(
                        Payment.participant_id == participant.id,
                        Payment.status == PaymentStatus.PENDING,
                    )
                    .order_by(Payment.id.desc())
                )
                .scalars()
                .first()
            )

    if payment is None:
        await message.answer(
            "Не нашёл неоплаченного счёта, к которому можно привязать квитанцию. "
            "Если вы уже оформили покупку и это не так — напишите в поддержку."
        )
        return

    original_filename: str | None
    content_type: str | None
    if message.photo:
        file_id = message.photo[-1].file_id
        original_filename = None
        content_type = "image/jpeg"
    elif message.document:
        file_id = message.document.file_id
        original_filename = message.document.file_name
        content_type = message.document.mime_type
    else:
        return

    import io

    from app.core.config import get_settings
    from app.services import receipt_service

    bot = _get_channel().bot
    file = await bot.get_file(file_id)
    if file.file_path is None:
        await message.answer("Не удалось скачать файл квитанции. Попробуйте прислать ещё раз.")
        return
    buffer = io.BytesIO()
    await bot.download_file(file.file_path, destination=buffer)

    receipt_service.save_receipt(
        db,
        get_settings(),
        payment_id=payment.id,
        content=buffer.getvalue(),
        content_type=content_type,
        original_filename=original_filename,
        telegram_file_id=file_id,
    )
    await message.answer(
        "Квитанция получена, спасибо! Номера придут после зачисления денег на "
        "расчётный счёт — это может занять несколько дней."
    )


@router.message(RegistrationStates.awaiting_phone)
async def on_phone_typed_for_registration(message: Message, state: FSMContext) -> None:
    """Ручной ввод номера при первом /start вместо «Поделиться контактом» —
    доступен, только если открыт из on_start (флаг ignore_phone_verification
    включён, п.7.1 ТЗ)."""
    db = get_channel_db()
    with db.session() as session:
        try:
            result = participant_service.bind_channel_ignoring_verification(
                session,
                channel=ChannelType.TELEGRAM,
                external_user_id=_uid(message),
                phone_raw=message.text or "",
                username=message.from_user.username if message.from_user else None,
            )
        except InvalidPhoneError:
            await message.answer("Не удалось распознать номер телефона. Попробуйте ещё раз.")
            return
        if result.conflict:
            logger.warning(
                "channel_rebind_conflict",
                external_user_id=_uid(message),
                phone=message.text,
            )
            await message.answer(
                "Этот телеграм-аккаунт уже привязан к другому номеру. Обратитесь к оператору."
            )
            return
        has_name = bool(result.participant.full_name)

    if has_name:
        channel = _get_channel()
        keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
        await state.clear()
        await message.answer(
            "Номер принят! Теперь вам доступна история покупок.",
            reply_markup=keyboard,
        )
        return

    await message.answer("Номер принят! Как вас зовут?")
    await state.set_state(RegistrationStates.awaiting_name)


@router.message(RegistrationStates.awaiting_name)
async def on_name_entered(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Пожалуйста, введите имя текстом.")
        return

    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id=_uid(message)
        )
        # Это состояние достижимо только когда номер уже подтверждён — либо
        # только что в on_contact, либо ранее (доспрашиваем на /start).
        assert participant is not None
        participant_service.set_full_name(session, participant_id=participant.id, full_name=name)

    await state.clear()
    channel = _get_channel()
    keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
    await message.answer(
        f"Приятно познакомиться, {name}! Теперь вам доступна история покупок.",
        reply_markup=keyboard,
    )


def _open_giveaways(session: Session) -> list[Giveaway]:
    giveaways = list(
        session.execute(
            select(Giveaway).where(
                Giveaway.is_registration_open.is_(True), Giveaway.is_locked.is_(False)
            )
        ).scalars()
    )
    return [g for g in giveaways if g.free_tickets_count > 0]


async def _prompt_giveaway_choice(
    message: Message, state: FSMContext, giveaways: list[Giveaway]
) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [
            InlineKeyboardButton(
                text=f"{g.name} ({g.free_tickets_count} своб.)", callback_data=f"giveaway:{g.id}"
            )
        ]
        for g in giveaways
    ]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="nav:back:menu")])
    await message.answer(
        "Выберите постер:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await state.set_state(PurchaseStates.choosing_giveaway)


@router.message(F.text == "🖼 Купить постер")
async def on_buy(message: Message, state: FSMContext) -> None:
    db = get_channel_db()
    with db.session() as session:
        giveaways = _open_giveaways(session)

    if not giveaways:
        await message.answer("Сейчас нет постеров, доступных для покупки.")
        return

    if len(giveaways) == 1:
        await _prompt_quantity(message, state, giveaways[0])
        return

    await _prompt_giveaway_choice(message, state, giveaways)


async def _prompt_quantity(message: Message, state: FSMContext, giveaway: Giveaway) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    db = get_channel_db()
    with db.session() as session:
        other_open_giveaways = _open_giveaways(session)
    back_target = "nav:back:giveaways" if len(other_open_giveaways) > 1 else "nav:back:menu"

    options = [q for q in QUANTITY_OPTIONS if q <= giveaway.free_tickets_count]
    rows = [
        [InlineKeyboardButton(text=str(q), callback_data=f"qty:{giveaway.id}:{q}") for q in options]
    ]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_target)])
    await message.answer(
        f"«{giveaway.name}»: цена экземпляра {giveaway.ticket_price / 100:.2f} ₽. "
        f"Сколько экземпляров хотите приобрести? (доступно {giveaway.free_tickets_count})\n"
        "Выберите вариант или введите число.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.set_state(PurchaseStates.choosing_quantity)
    await state.update_data(giveaway_id=giveaway.id)


@router.callback_query(F.data.startswith("giveaway:"))
async def on_giveaway_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    assert callback.data is not None
    giveaway_id = int(callback.data.split(":")[1])
    db = get_channel_db()
    with db.session() as session:
        giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        await callback.answer("Постер не найден", show_alert=True)
        return
    await _prompt_quantity(_msg(callback), state, giveaway)
    await callback.answer()


@router.callback_query(F.data == "nav:back:menu")
async def on_back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    channel = _get_channel()
    keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
    await _msg(callback).answer("Главное меню:", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "nav:back:giveaways")
async def on_back_to_giveaways(callback: CallbackQuery, state: FSMContext) -> None:
    db = get_channel_db()
    with db.session() as session:
        giveaways = _open_giveaways(session)
    if not giveaways:
        await state.clear()
        await callback.answer("Сейчас нет постеров, доступных для покупки.", show_alert=True)
        return
    await _prompt_giveaway_choice(_msg(callback), state, giveaways)
    await callback.answer()


@router.callback_query(F.data.startswith("nav:back:quantity:"))
async def on_back_to_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    assert callback.data is not None
    giveaway_id = int(callback.data.split(":")[3])
    db = get_channel_db()
    with db.session() as session:
        giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        await state.clear()
        await callback.answer("Постер недоступен.", show_alert=True)
        return
    await _prompt_quantity(_msg(callback), state, giveaway)
    await callback.answer()


async def _handle_quantity_selected(
    reply_target: Message, uid: str, state: FSMContext, giveaway_id: int, quantity: int
) -> None:
    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id=uid
        )

    if participant is not None:
        await _prompt_confirm_order(reply_target, state, giveaway_id, quantity, participant.id)
        return

    # Подарочная покупка на неподтверждённый номер (п.7.1, 10.3, 10.5 ТЗ): просим
    # ввести номер получателя вручную — своей учётки у покупателя ещё нет.
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await state.update_data(giveaway_id=giveaway_id, quantity=quantity)
    await state.set_state(PurchaseStates.awaiting_phone_for_gift)
    await reply_target.answer(
        "Введите номер телефона получателя постеров (формат: +7XXXXXXXXXX). "
        "Постер и коды придут в этот чат.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Назад", callback_data=f"nav:back:quantity:{giveaway_id}"
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith("qty:"))
async def on_quantity_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    assert callback.data is not None
    _, giveaway_id_raw, qty_raw = callback.data.split(":")
    giveaway_id, quantity = int(giveaway_id_raw), int(qty_raw)
    await _handle_quantity_selected(_msg(callback), _uid(callback), state, giveaway_id, quantity)
    await callback.answer()


@router.message(PurchaseStates.choosing_quantity)
async def on_quantity_typed(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) == 0:
        await message.answer("Введите количество экземпляров числом (больше нуля).")
        return

    data = await state.get_data()
    giveaway_id = data["giveaway_id"]
    await _handle_quantity_selected(message, _uid(message), state, giveaway_id, int(text))


@router.message(PurchaseStates.awaiting_phone_for_gift)
async def on_phone_entered(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    giveaway_id, quantity = data["giveaway_id"], data["quantity"]

    db = get_channel_db()
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
        try:
            if platform_settings.ignore_phone_verification:
                result = participant_service.bind_channel_ignoring_verification(
                    session,
                    channel=ChannelType.TELEGRAM,
                    external_user_id=_uid(message),
                    phone_raw=message.text or "",
                )
                participant_id = result.participant.id
            else:
                participant = participant_service.resolve_manual_recipient(
                    session, message.text or ""
                )
                participant_id = participant.id
        except InvalidPhoneError:
            await message.answer("Не удалось распознать номер телефона. Попробуйте ещё раз.")
            return

    await _prompt_confirm_order(message, state, giveaway_id, quantity, participant_id)


async def _prompt_confirm_order(
    reply_target: Message,
    state: FSMContext,
    giveaway_id: int,
    quantity: int,
    participant_id: int,
) -> None:
    """Шаг подтверждения перед резервированием экземпляров и созданием платежа —
    случайный тап по количеству иначе сразу занимал бы экземпляры из общего пула."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    db = get_channel_db()
    with db.session() as session:
        giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        await state.clear()
        await reply_target.answer("Этот постер недоступен. Начните заново — «🖼 Купить постер».")
        return

    total = giveaway.ticket_price * quantity / 100
    await state.update_data(
        giveaway_id=giveaway_id, quantity=quantity, participant_id=participant_id
    )
    await state.set_state(PurchaseStates.confirming_order)
    await reply_target.answer(
        f"«{giveaway.name}»: {quantity} экз. × {giveaway.ticket_price / 100:.2f} ₽ "
        f"= {total:.2f} ₽. Подтвердить покупку?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_order:yes")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="confirm_order:back")],
            ]
        ),
    )


@router.callback_query(F.data == "confirm_order:yes")
async def on_confirm_order_yes(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _create_and_offer_payment(
        _msg(callback), data["giveaway_id"], data["quantity"], data["participant_id"]
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "confirm_order:back")
async def on_confirm_order_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    db = get_channel_db()
    with db.session() as session:
        giveaway = session.get(Giveaway, data["giveaway_id"])
    if giveaway is None:
        await state.clear()
        await callback.answer("Постер недоступен.", show_alert=True)
        return
    await _prompt_quantity(_msg(callback), state, giveaway)
    await callback.answer()


async def _create_and_offer_payment(
    message: Message, giveaway_id: int, quantity: int, participant_id: int
) -> None:
    channel = _get_channel()
    db = get_channel_db()
    provider = get_active_provider(db)

    with db.session() as session:
        from app.models.participant import Participant

        participant = session.get(Participant, participant_id)
        phone = participant.phone if participant else ""

    async with channel.typing_action(chat_id=str(message.chat.id)):
        outcome = payment_svc.create_payment_safe(
            db,
            provider,
            giveaway_id=giveaway_id,
            participant_id=participant_id,
            participant_phone=phone,
            quantity=quantity,
            channel=ChannelType.TELEGRAM,
        )
    if not outcome.ok:
        if outcome.participant_blocked:
            await message.answer(
                "Ваш аккаунт заблокирован, покупка недоступна. Обратитесь в поддержку."
            )
        elif outcome.pending_limit_exceeded:
            await message.answer(
                f"У вас уже {outcome.pending_quantity} экз. в неоплаченных покупках "
                f"(максимум одновременно — {outcome.pending_limit}). Попробуйте выбрать "
                "меньшее количество или дождитесь оплаты/автоматической отмены текущих покупок."
            )
        else:
            await message.answer(
                "К сожалению, свободных экземпляров меньше, чем нужно "
                f"(доступно {outcome.free_count}). Попробуйте выбрать количество заново."
            )
        return

    assert outcome.created is not None
    assert outcome.order_id is not None
    assert outcome.amount is not None
    keyboard = channel.render_payment_prompt(
        payment_url=outcome.created.payment_url,
        order_id=outcome.order_id,
        has_qr=bool(outcome.created.qr_code_payload),
    )
    invoice_line = f" Счёт № {outcome.invoice_no}." if outcome.invoice_no else ""
    qr_payload = outcome.created.qr_code_payload
    qr_sent = False
    if qr_payload:
        # Присылаем QR сразу же, не дожидаясь нажатия «Показать QR» — кнопка
        # остаётся способом повторно запросить QR, в т.ч. если проактивная
        # отправка здесь не удастся (не должна ломать оформление заказа целиком).
        try:
            await channel.send_qr_code(
                str(message.chat.id),
                qr_payload,
                caption=(
                    "Отсканируйте QR-код в банковском приложении и оплатите по реквизитам.\n"
                    "После оплаты пришлите сюда квитанцию — номера придут после зачисления "
                    "денег на расчётный счёт (может занять несколько дней)."
                ),
            )
            qr_sent = True
        except Exception:
            logger.exception("telegram_proactive_qr_send_failed", order_id=outcome.order_id)
    if outcome.created.payment_url:
        instruction = (
            "Оплатите по ссылке ниже, либо QR-кодом выше (СБП)."
            if qr_sent
            else "Оплатите по ссылке ниже, либо нажмите «Показать QR» для оплаты по QR-коду (СБП)."
        )
    elif qr_sent:
        instruction = "Оплатите QR-код выше в банковском приложении по реквизитам."
    else:
        instruction = (
            "Нажмите «Показать QR» и оплатите по QR-коду в банковском приложении по "
            "реквизитам.\nПосле оплаты пришлите сюда квитанцию — номера придут после "
            "зачисления денег на расчётный счёт (может занять несколько дней)."
        )
    await message.answer(
        f"Счёт создан на {quantity} экз. на сумму {outcome.amount / 100:.2f} ₽.{invoice_line} "
        f"{instruction}",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("show_qr:"))
async def on_show_qr(callback: CallbackQuery) -> None:
    assert callback.data is not None
    order_id = callback.data.split(":", 1)[1]

    from app.models.payment import Payment

    db = get_channel_db()
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.order_id == order_id)
        ).scalar_one_or_none()
        qr_payload = payment.qr_code_payload if payment else None

    if not qr_payload:
        await callback.answer("QR недоступен для этого платежа.", show_alert=True)
        return

    await _get_channel().send_qr_code(str(_msg(callback).chat.id), qr_payload)
    await callback.answer()


@router.callback_query(F.data == "check_payment")
async def on_check_payment(callback: CallbackQuery) -> None:
    """Резервная проверка оплаты (п.7.5, 8.1, 10.2 ТЗ)."""
    db = get_channel_db()

    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id=_uid(callback)
        )
        if participant is None:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        from app.models.enums import PaymentStatus as _PS
        from app.models.payment import Payment

        payment = (
            session.execute(
                select(Payment)
                .where(Payment.participant_id == participant.id, Payment.status == _PS.PENDING)
                .order_by(Payment.id.desc())
            )
            .scalars()
            .first()
        )

    if payment is None:
        await callback.answer("Ожидающих оплаты счетов не найдено.", show_alert=True)
        return

    # Сверяем статус ЧЕРЕЗ ТОТ БАНК, которым платёж был создан (`payment.provider`),
    # а не через текущий активный провайдер — активный провайдер мог смениться в
    # панели (Super Admin) после создания этого платежа (п.9.3 ТЗ, DECISIONS.md).
    provider = get_provider_for_type(payment.provider)
    try:
        async with _get_channel().typing_action(chat_id=str(_msg(callback).chat.id)):
            bank_status = provider.check_status(
                payment.order_id, external_payment_id=payment.external_payment_id
            )
    except Exception:
        logger.exception("check_payment_status_failed", payment_id=payment.id)
        await callback.answer(
            "Не удалось проверить статус оплаты у банка. Попробуйте чуть позже.", show_alert=True
        )
        return
    if bank_status == PaymentStatus.PENDING:
        await callback.answer("Оплата ещё не поступила. Попробуйте чуть позже.", show_alert=True)
        return

    outcome = payment_svc.finalize_payment(db, order_id=payment.order_id, new_status=bank_status)
    if outcome.applied and outcome.new_status == PaymentStatus.SUCCEEDED:
        await _deliver_tickets(_msg(callback), outcome)
        await callback.answer("Оплата подтверждена!")
    elif outcome.new_status == PaymentStatus.FAILED:
        await callback.answer("Платёж не прошёл.", show_alert=True)
    else:
        await callback.answer("Статус пока без изменений.", show_alert=True)


async def _deliver_tickets(message: Message, outcome: payment_svc.FinalizeOutcome) -> None:
    channel = _get_channel()
    db = get_channel_db()
    with db.session() as session:
        giveaway = session.get(Giveaway, outcome.giveaway_id)
    codes = [t.full_code for t in (outcome.tickets or [])]
    await channel.deliver_purchase(
        str(message.chat.id),
        poster_path=giveaway.digital_poster_path if giveaway else None,
        codes=codes,
        intro="Оплата прошла успешно! Ваши номера:",
    )


@router.message(F.text == "📋 Мои покупки")
async def on_my_tickets(message: Message) -> None:
    db = get_channel_db()
    with db.session() as session:
        binding = participant_service.get_binding(
            session, channel=ChannelType.TELEGRAM, external_user_id=_uid(message)
        )
        platform_settings = settings_service.get_or_create_settings(session)
        if binding is None or not participant_service.can_access_own_account(
            binding, ignore_phone_verification=platform_settings.ignore_phone_verification
        ):
            await message.answer("Чтобы увидеть историю покупок, поделитесь контактом.")
            return
        tickets = list(
            session.execute(
                select(Ticket).where(Ticket.participant_id == binding.participant_id)
            ).scalars()
        )

    if not tickets:
        await message.answer("У вас пока нет покупок.")
        return
    await message.answer(f"Ваши номера ({len(tickets)}):")
    await _get_channel().send_ticket_codes(str(message.chat.id), [t.full_code for t in tickets])


@router.message(F.text == "ℹ️ Помощь")
@router.message(Command("help"))
async def on_help(message: Message) -> None:
    db = get_channel_db()
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
    contacts = platform_settings.support_contacts or {}
    lines = [
        "Справка по боту:",
        "— «Купить постер» — приобрести постер с уникальным номером.",
        "— «Мои покупки» — история покупок (после того как вы поделитесь контактом).",
    ]
    if contacts:
        lines.append("\nПоддержка:")
        for key, value in contacts.items():
            lines.append(f"{key}: {value}")
    await message.answer("\n".join(lines))


@router.message()
async def on_unhandled_message(message: Message) -> None:
    """Подстраховка вместо тишины: срабатывает только когда сообщение не подошло
    ни под один фильтр/состояние выше (регистрация в порядке @router.message() —
    последний хендлер получает то, что не разобрали остальные)."""
    channel = _get_channel()
    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id=_uid(message)
        )

    if participant is not None and participant.full_name is not None:
        keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
        await message.answer(
            "Не понял команду. Воспользуйтесь кнопками ниже или напишите /help.",
            reply_markup=keyboard,
        )
    else:
        await message.answer("Не понял команду. Напишите /start, чтобы продолжить.")


@router.callback_query()
async def on_unhandled_callback(callback: CallbackQuery) -> None:
    """Устаревшая инлайн-кнопка (сообщение из прошлого шага диалога) — не подошла
    ни под один фильтр выше."""
    await callback.answer("Это действие больше недоступно. Напишите /start.", show_alert=True)
