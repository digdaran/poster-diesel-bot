"""Обработчики диалога VK-бота (п.8.1, 10.2-10.6 ТЗ) — структура и бизнес-сценарий
идентичны `channels/telegram/handlers.py`, отличается только транспорт (vkbottle,
Long Poll) и способ показа инлайн-кнопок (VK message_event вместо
callback_query, см. ARCHITECTURE.md §7.1, DECISIONS_LOG.md #32).

Вся бизнес-логика — вызовы app/services/*; здесь только приём событий и
отрисовка ответов средствами vkbottle. Канал создаётся один раз в main.py и
передаётся сюда через `set_channel()`, как и у Telegram-канала.
"""

from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING, Any

import structlog
from app.core.phone import InvalidPhoneError
from app.models.enums import ChannelType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.ticket import Ticket
from app.services import participant_service, settings_service
from app.services import payment_service as payment_svc
from sqlalchemy import select
from sqlalchemy.orm import Session
from vkbottle import Callback, GroupEventType, GroupTypes, Keyboard, KeyboardButtonColor
from vkbottle.bot import BotLabeler, Message

from channels.vk.state import (
    QUANTITY_OPTIONS,
    PurchaseStates,
    RegistrationStates,
    get_active_provider,
    get_channel_db,
)

if TYPE_CHECKING:
    from channels.vk.channel import VkChannel

logger = structlog.get_logger(__name__)
labeler = BotLabeler()

_MAIN_KEYBOARD_BUTTONS = [
    ["🖼 Купить постер", "📋 Мои покупки"],
    ["💬 Написать в поддержку", "ℹ️ Помощь"],
]
_START_TEXTS = {"/start", "начать", "start"}
_HELP_TEXTS = {"ℹ️ помощь", "/help", "помощь"}
_BUY_TEXT = "🖼 Купить постер"
_MY_TICKETS_TEXT = "📋 Мои покупки"
_SUPPORT_CONTACT_TEXT = "💬 Написать в поддержку"

# Кнопки главного меню должны работать как глобальная навигация, даже если
# участник застрял посреди диалога покупки (не долистал выбор количества/ввод
# номера получателя) — см. DECISIONS_LOG.md, аналогично Telegram-каналу.
# Используется, чтобы исключить эти тексты из хендлеров, привязанных к
# состоянию FSM (иначе перехватывают текст кнопки как невалидный ввод
# количества/телефона).
_MAIN_MENU_TEXTS = frozenset(text for row in _MAIN_KEYBOARD_BUTTONS for text in row)

# Переиспользуется в сообщении о принятой квитанции и в конце «Мои покупки»
# (и списка неоплаченных счетов, и списка уже выданных номерков) — подсказка
# оформить ещё один заказ.
_BUY_MORE_HINT = "Хотите приобрести ещё постеры — оформите новый заказ, нажав «🖼 Купить постер»."

_channel: VkChannel | None = None  # устанавливается через set_channel() в main.py


def set_channel(channel: VkChannel) -> None:
    global _channel
    _channel = channel


def _get_channel() -> VkChannel:
    if _channel is None:
        raise RuntimeError("VkChannel не инициализирован — вызовите set_channel() в main.py")
    return _channel


def _uid(peer_id: int) -> str:
    """VK-диалог с сообществом один на один — `peer_id` пользователя совпадает
    с его `from_id`, поэтому единый ID для идентификации и адресации."""
    return str(peer_id)


async def _get_state_payload(peer_id: int) -> dict[str, Any]:
    peer = await _get_channel().bot.state_dispenser.get(peer_id)
    return dict(peer.payload) if peer else {}


async def _set_state(peer_id: int, state: Any, **extra: Any) -> None:
    """В отличие от aiogram `FSMContext.update_data`, `state_dispenser.set`
    ЗАМЕНЯЕТ payload целиком — сливаем с уже накопленными данными вручную,
    чтобы поведение совпадало с Telegram-обработчиками того же сценария."""
    data = await _get_state_payload(peer_id)
    data.update(extra)
    await _get_channel().bot.state_dispenser.set(peer_id, state, **data)


async def _clear_state(peer_id: int) -> None:
    await _get_channel().bot.state_dispenser.delete(peer_id)


def _is_start(message: Message) -> bool:
    text = (message.text or "").strip().lower()
    if text in _START_TEXTS:
        return True
    payload = message.get_payload_json()
    # Payload кнопки "Начать" из настроек сообщества — см. DECISIONS_LOG.md #32
    # (требует сверки с реальным сообществом перед вводом в эксплуатацию,
    # как и остальные внешние допущения по VK API — см. DECISIONS_LOG.md #1).
    return isinstance(payload, dict) and payload.get("command") == "start"


def _is_help(message: Message) -> bool:
    return (message.text or "").strip().lower() in _HELP_TEXTS


def _is_receipt_upload(message: Message) -> bool:
    return any(a.photo is not None or a.doc is not None for a in (message.attachments or []))


def _is_not_main_menu_text(message: Message) -> bool:
    """Отсекает нажатия кнопок главного меню от FSM-хендлеров, привязанных к
    состоянию покупки, — см. _MAIN_MENU_TEXTS."""
    return (message.text or "") not in _MAIN_MENU_TEXTS


@labeler.message(func=_is_start)
async def on_start(message: Message) -> None:
    """Главная клавиатура (покупка/история/помощь) открывается только после
    ПОЛНОЙ регистрации — подтверждённый номер И указанное имя. До этого
    момента доступна только клавиатура текущего шага регистрации."""
    await _clear_state(message.peer_id)
    channel = _get_channel()
    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.VK, external_user_id=_uid(message.peer_id)
        )
        ignore_verification = settings_service.get_or_create_settings(
            session
        ).ignore_phone_verification

    if participant is None:
        await message.answer("Добро пожаловать в бот продажи цифровых постеров!")
        await channel.request_contact(_uid(message.peer_id))
        if ignore_verification:
            # П.7.1 ТЗ: при включённом флаге ручной ввод номера открывает доступ —
            # для VK это ЕДИНСТВЕННЫЙ путь получить доступ (нет request_contact).
            await message.answer("Напишите номер телефона в чат.")
            await _set_state(message.peer_id, RegistrationStates.AWAITING_PHONE)
        return

    if participant.full_name is None:
        await message.answer("Добро пожаловать в бот продажи цифровых постеров!")
        await message.answer("Как вас зовут?")
        await _set_state(message.peer_id, RegistrationStates.AWAITING_NAME)
        return

    keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
    await message.answer("Добро пожаловать в бот продажи цифровых постеров!", keyboard=keyboard)


@labeler.message(func=_is_receipt_upload)
async def on_receipt_upload(message: Message) -> None:
    """Квитанция об оплате, присланная участником. Если перед этим участник выбрал
    конкретный счёт в «Мои покупки» (payload `{"a": "select_payment_receipt"}` →
    `_dispatch_message_event`, id счёта лежит в данных состояния под ключом
    `receipt_payment_id`) — квитанция привязывается именно к нему. Иначе — к
    последнему неоплаченному счёту участника (`Payment.status == PENDING`), как и
    раньше. См. `channels.telegram.handlers.on_receipt_upload` — тот же сценарий,
    зарегистрирован рано (как `on_start`), чтобы не оказаться в тени состояний
    диалога регистрации/покупки."""
    from app.models.payment import Payment

    state_data = await _get_state_payload(message.peer_id)
    selected_payment_id = state_data.get("receipt_payment_id")

    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.VK, external_user_id=_uid(message.peer_id)
        )
        payment = None
        if participant is not None:
            if selected_payment_id is not None:
                payment = session.execute(
                    select(Payment).where(
                        Payment.id == selected_payment_id,
                        Payment.participant_id == participant.id,
                        Payment.status == PaymentStatus.PENDING,
                    )
                ).scalar_one_or_none()
            else:
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
        if selected_payment_id is not None:
            await _clear_state(message.peer_id)
            await message.answer(
                "Этот счёт уже недоступен для прикрепления квитанции (возможно, статус "
                "изменился). Откройте «Мои покупки» и попробуйте снова."
            )
            return
        await message.answer(
            "Не нашёл неоплаченного счёта, к которому можно привязать квитанцию. "
            "Если вы уже оформили покупку и это не так — напишите в поддержку."
        )
        return

    attachment = next(
        a for a in (message.attachments or []) if a.photo is not None or a.doc is not None
    )
    original_filename: str | None = None
    content_type: str | None = None
    vk_attachment: str | None = None
    url: str | None = None
    if attachment.photo is not None:
        photo = attachment.photo
        sizes = photo.sizes or []
        largest = max(sizes, key=lambda s: (s.width or 0) * (s.height or 0), default=None)
        url = largest.url if largest else None
        content_type = "image/jpeg"
        vk_attachment = f"photo{photo.owner_id}_{photo.id}"
    elif attachment.doc is not None:
        doc = attachment.doc
        url = doc.url
        original_filename = doc.title
        vk_attachment = f"doc{doc.owner_id}_{doc.id}"

    if not url:
        await message.answer("Не удалось обработать вложение. Попробуйте прислать ещё раз.")
        return

    import httpx
    from app.core.config import get_settings
    from app.services import receipt_service

    # follow_redirects=True: VK-ссылки на документы (в отличие от фото) отдают
    # 302 на реальный CDN-URL — без этого httpx не идёт по редиректу, а
    # response.raise_for_status() падает на самом 302 как на ошибке.
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        content = response.content

    receipt_service.save_receipt(
        db,
        get_settings(),
        payment_id=payment.id,
        content=content,
        content_type=content_type,
        original_filename=original_filename,
        vk_attachment=vk_attachment,
    )
    if selected_payment_id is not None:
        await _clear_state(message.peer_id)
    await message.answer(
        "Квитанция получена, спасибо! Больше ничего делать не нужно — оплачивать повторно "
        "не надо. Номера придут после зачисления денег на расчётный счёт (как правило, до "
        "30 минут, в редких случаях — до 3 дней). " + _BUY_MORE_HINT
    )


@labeler.message(state=RegistrationStates.AWAITING_PHONE)
async def on_phone_typed_for_registration(message: Message) -> None:
    """Ручной ввод номера — единственный путь регистрации в VK
    (`supports_verified_phone=False`), доступен только при включённом
    `ignore_phone_verification` (п.7.1 ТЗ, ARCHITECTURE.md §7.1)."""
    db = get_channel_db()
    with db.session() as session:
        try:
            result = participant_service.bind_channel_ignoring_verification(
                session,
                channel=ChannelType.VK,
                external_user_id=_uid(message.peer_id),
                phone_raw=message.text or "",
            )
        except InvalidPhoneError:
            await message.answer("Не удалось распознать номер телефона. Попробуйте ещё раз.")
            return
        if result.conflict:
            logger.warning(
                "channel_rebind_conflict",
                external_user_id=_uid(message.peer_id),
                phone=message.text,
            )
            await message.answer(
                "Этот аккаунт ВКонтакте уже привязан к другому номеру. Обратитесь к оператору."
            )
            return
        has_name = bool(result.participant.full_name)

    if has_name:
        channel = _get_channel()
        keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
        await _clear_state(message.peer_id)
        await message.answer(
            "Номер принят! Теперь вам доступна история покупок.", keyboard=keyboard
        )
        return

    await message.answer("Номер принят! Как вас зовут?")
    await _set_state(message.peer_id, RegistrationStates.AWAITING_NAME)


@labeler.message(state=RegistrationStates.AWAITING_NAME)
async def on_name_entered(message: Message) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Пожалуйста, введите имя текстом.")
        return

    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.VK, external_user_id=_uid(message.peer_id)
        )
        assert participant is not None
        participant_service.set_full_name(session, participant_id=participant.id, full_name=name)

    await _clear_state(message.peer_id)
    channel = _get_channel()
    keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
    await message.answer(
        f"Приятно познакомиться, {name}! Регистрация завершена. Теперь вы можете покупать "
        "постеры и в любой момент смотреть историю покупок и статус счетов — просто "
        "воспользуйтесь кнопками меню ниже.",
        keyboard=keyboard,
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
    peer_id: int, giveaways: list[Giveaway], *, answer_target: Message | None = None
) -> None:
    keyboard = Keyboard(inline=True)
    for index, g in enumerate(giveaways):
        if index:
            keyboard.row()
        keyboard.add(
            Callback(
                f"{g.name} ({g.free_tickets_count} своб.)",
                payload={"a": "giveaway", "id": g.id},
            )
        )
    keyboard.row()
    keyboard.add(Callback("◀️ Назад", payload={"a": "nav", "to": "menu"}))
    text = "Выберите постер:"
    if answer_target is not None:
        await answer_target.answer(text, keyboard=keyboard.get_json())
    else:
        await _get_channel().send_message(_uid(peer_id), text, keyboard=keyboard.get_json())
    await _set_state(peer_id, PurchaseStates.CHOOSING_GIVEAWAY)


@labeler.message(text=_BUY_TEXT)
async def on_buy(message: Message) -> None:
    # Глобальная кнопка меню — молча сбрасывает любой незавершённый диалог
    # (покупку/регистрацию) и начинает выбор постера заново.
    await _clear_state(message.peer_id)
    db = get_channel_db()
    with db.session() as session:
        giveaways = _open_giveaways(session)

    if not giveaways:
        await message.answer("Сейчас нет постеров, доступных для покупки.")
        return

    if len(giveaways) == 1:
        await _prompt_quantity(message.peer_id, giveaways[0], answer_target=message)
        return

    await _prompt_giveaway_choice(message.peer_id, giveaways, answer_target=message)


async def _prompt_quantity(
    peer_id: int, giveaway: Giveaway, *, answer_target: Message | None = None
) -> None:
    db = get_channel_db()
    with db.session() as session:
        other_open_giveaways = _open_giveaways(session)
    back_to = "giveaways" if len(other_open_giveaways) > 1 else "menu"

    options = [q for q in QUANTITY_OPTIONS if q <= giveaway.free_tickets_count]
    keyboard = Keyboard(inline=True)
    for index, q in enumerate(options):
        if index:
            pass  # варианты количества — в одной строке
        keyboard.add(Callback(str(q), payload={"a": "qty", "id": giveaway.id, "q": q}))
    keyboard.row()
    keyboard.add(Callback("◀️ Назад", payload={"a": "nav", "to": back_to}))

    text = (
        f"«{giveaway.name}»: цена экземпляра {giveaway.ticket_price / 100:.2f} ₽. "
        f"Сколько экземпляров хотите приобрести? (доступно {giveaway.free_tickets_count})\n"
        "Выберите вариант или введите число."
    )
    if answer_target is not None:
        await answer_target.answer(text, keyboard=keyboard.get_json())
    else:
        await _get_channel().send_message(_uid(peer_id), text, keyboard=keyboard.get_json())
    await _set_state(peer_id, PurchaseStates.CHOOSING_QUANTITY, giveaway_id=giveaway.id)


async def _handle_quantity_selected(
    peer_id: int, giveaway_id: int, quantity: int, *, answer_target: Message | None = None
) -> None:
    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.VK, external_user_id=_uid(peer_id)
        )

    if participant is not None:
        await _prompt_confirm_order(
            peer_id, giveaway_id, quantity, participant.id, answer_target=answer_target
        )
        return

    # Подарочная покупка на неподтверждённый номер (п.7.1, 10.3, 10.5 ТЗ): просим
    # ввести номер получателя вручную — своей учётки у покупателя ещё нет.
    keyboard = Keyboard(inline=True)
    keyboard.add(Callback("◀️ Назад", payload={"a": "nav", "to": "quantity", "id": giveaway_id}))
    text = (
        "Введите номер телефона получателя постеров (формат: +7XXXXXXXXXX). "
        "Постеры и номера придут в этот чат."
    )
    if answer_target is not None:
        await answer_target.answer(text, keyboard=keyboard.get_json())
    else:
        await _get_channel().send_message(_uid(peer_id), text, keyboard=keyboard.get_json())
    await _set_state(
        peer_id, PurchaseStates.AWAITING_PHONE_FOR_GIFT, giveaway_id=giveaway_id, quantity=quantity
    )


@labeler.message(state=PurchaseStates.CHOOSING_QUANTITY, func=_is_not_main_menu_text)
async def on_quantity_typed(message: Message) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) == 0:
        await message.answer("Введите количество экземпляров числом (больше нуля).")
        return

    data = await _get_state_payload(message.peer_id)
    await _handle_quantity_selected(
        message.peer_id, data["giveaway_id"], int(text), answer_target=message
    )


@labeler.message(state=PurchaseStates.AWAITING_PHONE_FOR_GIFT, func=_is_not_main_menu_text)
async def on_phone_entered(message: Message) -> None:
    data = await _get_state_payload(message.peer_id)
    giveaway_id, quantity = data["giveaway_id"], data["quantity"]

    db = get_channel_db()
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
        try:
            if platform_settings.ignore_phone_verification:
                result = participant_service.bind_channel_ignoring_verification(
                    session,
                    channel=ChannelType.VK,
                    external_user_id=_uid(message.peer_id),
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

    await _prompt_confirm_order(
        message.peer_id, giveaway_id, quantity, participant_id, answer_target=message
    )


async def _prompt_confirm_order(
    peer_id: int,
    giveaway_id: int,
    quantity: int,
    participant_id: int,
    *,
    answer_target: Message | None = None,
) -> None:
    """Шаг подтверждения перед резервированием номерков и созданием платежа —
    случайный тап по количеству иначе сразу занимал бы номерки из общего пула."""
    db = get_channel_db()
    with db.session() as session:
        giveaway = session.get(Giveaway, giveaway_id)
    if giveaway is None:
        await _clear_state(peer_id)
        text = "Этот постер недоступен. Начните заново — «🖼 Купить постер»."
        if answer_target is not None:
            await answer_target.answer(text)
        else:
            await _get_channel().send_message(_uid(peer_id), text)
        return

    total = giveaway.ticket_price * quantity / 100
    await _set_state(
        peer_id,
        PurchaseStates.CONFIRMING_ORDER,
        giveaway_id=giveaway_id,
        quantity=quantity,
        participant_id=participant_id,
    )
    keyboard = Keyboard(inline=True)
    keyboard.add(
        Callback("✅ Подтвердить", payload={"a": "confirm_order", "v": "yes"}),
        color=KeyboardButtonColor.POSITIVE,
    )
    keyboard.row()
    keyboard.add(Callback("◀️ Назад", payload={"a": "confirm_order", "v": "back"}))
    text = (
        f"«{giveaway.name}»: {quantity} экз. × {giveaway.ticket_price / 100:.2f} ₽ "
        f"= {total:.2f} ₽. Подтвердить покупку?"
    )
    if answer_target is not None:
        await answer_target.answer(text, keyboard=keyboard.get_json())
    else:
        await _get_channel().send_message(_uid(peer_id), text, keyboard=keyboard.get_json())


async def _create_and_offer_payment(
    peer_id: int, giveaway_id: int, quantity: int, participant_id: int
) -> None:
    channel = _get_channel()
    db = get_channel_db()
    provider = get_active_provider(db)

    with db.session() as session:
        from app.models.participant import Participant

        participant = session.get(Participant, participant_id)
        phone = participant.phone if participant else ""

    outcome = payment_svc.create_payment_safe(
        db,
        provider,
        giveaway_id=giveaway_id,
        participant_id=participant_id,
        participant_phone=phone,
        quantity=quantity,
        channel=ChannelType.VK,
        initiating_external_user_id=_uid(peer_id),
    )
    if not outcome.ok:
        if outcome.participant_blocked:
            await channel.send_message(
                _uid(peer_id),
                "Ваш аккаунт заблокирован, покупка недоступна. Обратитесь в поддержку.",
            )
        elif outcome.pending_limit_exceeded:
            await channel.send_message(
                _uid(peer_id),
                f"У вас уже {outcome.pending_quantity} экз. в неоплаченных покупках "
                f"(максимум одновременно — {outcome.pending_limit}). Попробуйте выбрать "
                "меньшее количество или дождитесь, пока текущие покупки будут оплачены "
                "или автоматически отменены.",
            )
        else:
            await channel.send_message(
                _uid(peer_id),
                "К сожалению, свободных экземпляров меньше, чем нужно "
                f"(доступно {outcome.free_count}). Попробуйте выбрать количество заново.",
            )
        return

    assert outcome.created is not None
    assert outcome.order_id is not None
    assert outcome.amount is not None
    keyboard = channel.render_payment_prompt(payment_url=outcome.created.payment_url)
    invoice_line = f" Счёт № {outcome.invoice_no}." if outcome.invoice_no else ""
    qr_payload = outcome.created.qr_code_payload
    qr_sent = False
    if qr_payload:
        # QR — единственный способ получить платёжные реквизиты (нет кнопки
        # повторного показа, см. DECISIONS_LOG.md), поэтому при сетевой ошибке
        # пробуем ещё раз, прежде чем признать отправку неудавшейся.
        for attempt in range(2):
            try:
                await channel.send_qr_code(
                    _uid(peer_id),
                    qr_payload,
                    caption=(
                        "Отсканируйте QR-код в банковском приложении и оплатите по реквизитам.\n"
                        "Этот QR-код действителен только для данного счёта: не используйте его "
                        "повторно для оплаты другого заказа и не меняйте сумму или назначение "
                        "платежа — иначе оплата не будет засчитана автоматически.\n"
                        "После оплаты пришлите сюда квитанцию — постеры с присвоенными номерами "
                        "придут после зачисления денег на расчётный счёт (как правило, до 30 "
                        "минут, в редких случаях — до 3 дней)."
                    ),
                )
                qr_sent = True
                break
            except Exception:
                if attempt == 1:
                    logger.exception("vk_proactive_qr_send_failed", order_id=outcome.order_id)
    if outcome.created.payment_url:
        instruction = (
            "Оплатите по ссылке ниже, либо QR-кодом выше (СБП)."
            if qr_sent
            else "Оплатите по ссылке ниже (СБП)."
        )
    elif qr_sent:
        instruction = "Оплатите QR-код выше в банковском приложении по реквизитам."
    else:
        instruction = (
            "Не удалось отправить QR-код для оплаты. Пожалуйста, воспользуйтесь кнопкой "
            "«💬 Написать в поддержку», чтобы получить реквизиты для оплаты вручную."
        )
    await channel.send_message(
        _uid(peer_id),
        f"Счёт создан на {quantity} экз. на сумму {outcome.amount / 100:.2f} ₽."
        f"{invoice_line} {instruction}",
        keyboard=keyboard,
    )


async def _deliver_tickets(peer_id: int, outcome: payment_svc.FinalizeOutcome) -> None:
    channel = _get_channel()
    db = get_channel_db()
    with db.session() as session:
        giveaway = session.get(Giveaway, outcome.giveaway_id)
        poster_path = (
            random.choice([p.file_path for p in giveaway.posters])
            if giveaway is not None and giveaway.posters
            else None
        )
    codes = [t.full_code for t in (outcome.tickets or [])]
    await channel.deliver_purchase(
        _uid(peer_id),
        poster_path=poster_path,
        codes=codes,
        intro="Оплата прошла успешно! Ваши постеры куплены, номера:",
    )


@labeler.message(text=_MY_TICKETS_TEXT)
async def on_my_tickets(message: Message) -> None:
    # Глобальная кнопка меню — доступна и посреди незавершённой покупки, молча
    # сбрасывая её (см. _MAIN_MENU_TEXTS).
    await _clear_state(message.peer_id)
    db = get_channel_db()
    with db.session() as session:
        binding = participant_service.get_binding(
            session, channel=ChannelType.VK, external_user_id=_uid(message.peer_id)
        )
        platform_settings = settings_service.get_or_create_settings(session)
        if binding is None or not participant_service.can_access_own_account(
            binding, ignore_phone_verification=platform_settings.ignore_phone_verification
        ):
            await message.answer(
                "Доступ к истории покупок открывается, когда вы укажете номер телефона. "
                "Напишите «Начать», чтобы продолжить."
            )
            return
        participant_id = binding.participant_id
        tickets = list(
            session.execute(select(Ticket).where(Ticket.participant_id == participant_id)).scalars()
        )

    from app.core.config import get_settings

    pending_payments = payment_svc.list_pending_payments(
        db, get_settings(), participant_id=participant_id
    )

    if not tickets and not pending_payments:
        await message.answer("У вас пока нет покупок.")
        return

    if tickets:
        await message.answer(f"Ваши постеры ({len(tickets)} шт.), номера:")
        await _get_channel().send_ticket_codes(
            _uid(message.peer_id), [t.full_code for t in tickets]
        )

    if pending_payments:
        await _send_pending_payments(message.peer_id, pending_payments, answer_target=message)
    else:
        # Если неоплаченных счетов нет, подсказка про новый заказ иначе не
        # попала бы в вывод «Мои покупки» — при pending_payments она уже есть
        # в конце _send_pending_payments.
        await message.answer(_BUY_MORE_HINT)


async def _send_pending_payments(
    peer_id: int,
    payments: list[payment_svc.PendingPaymentInfo],
    *,
    answer_target: Message | None = None,
) -> None:
    """Список неоплаченных счетов участника (раздел «Мои покупки») — для тех, у
    которых ещё нет квитанции, добавляется кнопка выбора счёта для её
    прикрепления (см. `_dispatch_message_event`, action `select_payment_receipt`)."""
    lines = ["Неоплаченные счета:"]
    keyboard = Keyboard(inline=True)
    has_buttons = False
    for index, p in enumerate(payments):
        invoice_line = f", счёт № {p.invoice_no}" if p.invoice_no else ""
        receipt_line = "квитанция получена ✅" if p.has_receipt else "квитанция не прислана ⏳"
        expiry_line = (
            f", действителен до {p.expires_at:%d.%m.%Y}" if p.expires_at is not None else ""
        )
        # Пустая строка перед КАЖДЫМ счётом, кроме первого — иначе строки
        # разных заказов слипаются друг с другом визуально.
        prefix = "\n" if index > 0 else ""
        lines.append(
            f"{prefix}«{p.giveaway_name}»: {p.quantity} экз. на {p.amount / 100:.2f} ₽"
            f"{invoice_line} — {receipt_line}{expiry_line}"
        )
        if not p.has_receipt:
            if has_buttons:
                keyboard.row()
            # Без номера счёта: VK жёстко ограничивает label кнопки 40 символами
            # (VKAPIError_911), а `invoice_line` с длинным префиксом розыгрыша
            # легко превышает лимит — сумма (в отличие от номера счёта) уже
            # достаточно, чтобы отличить кнопки друг от друга, полная строка с
            # номером счёта и так есть в тексте выше.
            keyboard.add(
                Callback(
                    f"📎 Прислать квитанцию — {p.amount / 100:.2f} ₽",
                    payload={"a": "select_payment_receipt", "id": p.payment_id},
                )
            )
            has_buttons = True
    if any(p.has_receipt for p in payments):
        lines.append(
            "\nСчета с отметкой «квитанция получена ✅» уже проверяются — платить повторно "
            "не нужно, дождитесь зачисления (как правило, до 30 минут, в редких случаях — "
            "до 3 дней)."
        )
    lines.append("\n" + _BUY_MORE_HINT)
    text = "\n".join(lines)
    keyboard_json = keyboard.get_json() if has_buttons else None
    if answer_target is not None:
        await answer_target.answer(text, keyboard=keyboard_json)
    else:
        await _get_channel().send_message(_uid(peer_id), text, keyboard=keyboard_json)


@labeler.message(text=_SUPPORT_CONTACT_TEXT)
async def on_support_contact(message: Message) -> None:
    # Глобальная кнопка меню — доступна и посреди незавершённой покупки, молча
    # сбрасывая её (см. _MAIN_MENU_TEXTS).
    await _clear_state(message.peer_id)
    channel = _get_channel()
    db = get_channel_db()
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
    contacts = platform_settings.support_contacts or {}
    url = settings_service.support_contact_url(contacts, "vk")
    if url is None:
        await on_help(message)
        return
    keyboard = channel.render_support_prompt(url=url)
    await channel.send_message(
        _uid(message.peer_id),
        "Нажмите кнопку ниже, чтобы открыть чат с поддержкой.",
        keyboard=keyboard,
    )


@labeler.message(func=_is_help)
async def on_help(message: Message) -> None:
    # Глобальная кнопка меню — доступна и посреди незавершённой покупки, молча
    # сбрасывая её (см. _MAIN_MENU_TEXTS).
    await _clear_state(message.peer_id)
    db = get_channel_db()
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
    contacts = platform_settings.support_contacts or {}
    lines = [
        "Справка по боту:",
        "",
        "Как оформить покупку:",
        "— «Купить постер» — выберите количество и получите счёт с QR-кодом для оплаты по "
        "реквизитам.",
        "— Отсканируйте QR-код в приложении банка и подтвердите платёж. Ничего больше "
        "вводить и изменять не нужно.",
        "— Отправьте квитанцию об оплате в этот же диалог, где совершали покупку.",
        "— Постеры с присвоенными номерами придут автоматически, как только оплата "
        "поступит на расчётный счёт.",
        "",
        "«Мои покупки» — история покупок и статус счетов.",
        "«Написать в поддержку» — перенаправит вас на диалог с оператором.",
        "",
        "P.S. Оплата через Т-Банк: если переводите из приложения Т-Банка, комиссия за "
        "перевод не взимается, а платёж поступает быстрее, чем из других банков.",
        "Оплата наличными: чтобы оплатить наличными, обратитесь к оператору АЗС «Дизель»: "
        "г. Элиста, ул. Кирова, д. 155а.",
    ]
    if contacts:
        lines.append("\nПоддержка:")
        for key, value in contacts.items():
            lines.append(f"{key}: {value}")
    await message.answer("\n".join(lines))


@labeler.message()
async def on_unhandled_message(message: Message) -> None:
    """Подстраховка вместо тишины: срабатывает только когда сообщение не подошло
    ни под один фильтр/состояние выше (регистрация в порядке @labeler.message() —
    последний хендлер получает то, что не разобрали остальные)."""
    channel = _get_channel()
    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.VK, external_user_id=_uid(message.peer_id)
        )

    if participant is not None and participant.full_name is not None:
        keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
        await message.answer(
            "Не понял команду. Воспользуйтесь кнопками ниже или напишите «Помощь».",
            keyboard=keyboard,
        )
    else:
        await message.answer("Не понял команду. Напишите «Начать», чтобы продолжить.")


async def _answer_event(event: GroupTypes.MessageEvent, *, snackbar: str | None = None) -> None:
    """Закрывает "крутилку" загрузки на нажатой inline-кнопке (аналог
    `CallbackQuery.answer()` в Telegram). `snackbar`, если задан, показывает
    короткое всплывающее уведомление (аналог `show_alert=True`)."""
    bot = _get_channel().bot
    event_data = None
    if snackbar is not None:
        event_data = json.dumps({"type": "show_snackbar", "text": snackbar})
    await bot.api.messages.send_message_event_answer(
        event_id=event.object.event_id,
        peer_id=event.object.peer_id,
        user_id=event.object.user_id,
        event_data=event_data,
    )


async def _dispatch_message_event(event: GroupTypes.MessageEvent) -> None:
    """Единая точка обработки нажатий inline-кнопок (VK `message_event`, п.5.4.1
    ТЗ) — в отличие от Telegram `callback_query`, где каждой кнопке соответствует
    свой `@router.callback_query(...)`, ответ на VK-кнопку требует явного
    `messages.sendMessageEventAnswer` с `event_id`, поэтому маршрутизация
    выполняется вручную по `payload["a"]`, а не декораторами (см.
    ARCHITECTURE.md §7.1)."""
    obj = event.object
    payload = obj.payload or {}
    action = payload.get("a")
    peer_id = obj.peer_id
    db = get_channel_db()

    if action == "giveaway":
        giveaway_id = int(payload["id"])
        with db.session() as session:
            giveaway = session.get(Giveaway, giveaway_id)
        if giveaway is None:
            await _answer_event(event, snackbar="Постер не найден")
            return
        await _prompt_quantity(peer_id, giveaway)
        await _answer_event(event)
        return

    if action == "nav":
        to = payload.get("to")
        if to == "menu":
            await _clear_state(peer_id)
            channel = _get_channel()
            keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
            await channel.send_message(_uid(peer_id), "Главное меню:", keyboard=keyboard)
            await _answer_event(event)
            return
        if to == "giveaways":
            with db.session() as session:
                giveaways = _open_giveaways(session)
            if not giveaways:
                await _clear_state(peer_id)
                await _answer_event(event, snackbar="Сейчас нет постеров, доступных для покупки.")
                return
            await _prompt_giveaway_choice(peer_id, giveaways)
            await _answer_event(event)
            return
        if to == "quantity":
            giveaway_id = int(payload["id"])
            with db.session() as session:
                giveaway = session.get(Giveaway, giveaway_id)
            if giveaway is None:
                await _clear_state(peer_id)
                await _answer_event(event, snackbar="Постер недоступен.")
                return
            await _prompt_quantity(peer_id, giveaway)
            await _answer_event(event)
            return
        await _answer_event(event)
        return

    if action == "qty":
        giveaway_id, quantity = int(payload["id"]), int(payload["q"])
        await _handle_quantity_selected(peer_id, giveaway_id, quantity)
        await _answer_event(event)
        return

    if action == "confirm_order":
        if payload.get("v") == "yes":
            data = await _get_state_payload(peer_id)
            await _create_and_offer_payment(
                peer_id, data["giveaway_id"], data["quantity"], data["participant_id"]
            )
            await _clear_state(peer_id)
            await _answer_event(event)
            return
        # "back"
        data = await _get_state_payload(peer_id)
        with db.session() as session:
            giveaway = session.get(Giveaway, data["giveaway_id"])
        if giveaway is None:
            await _clear_state(peer_id)
            await _answer_event(event, snackbar="Постер недоступен.")
            return
        await _prompt_quantity(peer_id, giveaway)
        await _answer_event(event)
        return

    if action == "select_payment_receipt":
        payment_id = int(payload["id"])
        with db.session() as session:
            participant = participant_service.get_participant_by_channel(
                session, channel=ChannelType.VK, external_user_id=_uid(peer_id)
            )
        payment = None
        if participant is not None:
            payment = payment_svc.get_own_pending_payment(
                db, payment_id=payment_id, participant_id=participant.id
            )
        if payment is None:
            await _answer_event(event, snackbar="Этот счёт больше недоступен.")
            return
        await _set_state(peer_id, PurchaseStates.AWAITING_RECEIPT, receipt_payment_id=payment.id)
        invoice_line = (
            f" (счёт № {payment.giveaway.format_invoice_number(payment.payment_number)})"
            if payment.payment_number is not None
            else ""
        )
        await _get_channel().send_message(
            _uid(peer_id),
            f"Пришлите фото или документ с квитанцией к счёту на {payment.amount / 100:.2f} ₽"
            f"{invoice_line}.",
        )
        await _answer_event(event)
        return

    await _answer_event(event, snackbar="Это действие больше недоступно. Напишите «Начать».")


@labeler.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=GroupTypes.MessageEvent)
async def on_message_event(event: GroupTypes.MessageEvent) -> None:
    await _dispatch_message_event(event)


@labeler.raw_event(GroupEventType.MESSAGE_ALLOW, dataclass=GroupTypes.MessageAllow)
async def on_message_allow(event: GroupTypes.MessageAllow) -> None:
    """Пользователь разрешил сообществу писать первым (после написанного им
    самим сообщения либо через виджет) — см. DECISIONS_LOG.md #32."""
    await _set_messages_allowed(event.object.user_id, allowed=True)


@labeler.raw_event(GroupEventType.MESSAGE_DENY, dataclass=GroupTypes.MessageDeny)
async def on_message_deny(event: GroupTypes.MessageDeny) -> None:
    """Пользователь отозвал разрешение — проактивная отправка (см.
    `app/services/notification_service.py`) должна деградировать до
    реактивной доставки для этой привязки (см. DECISIONS_LOG.md #32)."""
    await _set_messages_allowed(event.object.user_id, allowed=False)


async def _set_messages_allowed(vk_user_id: int, *, allowed: bool) -> None:
    db = get_channel_db()
    with db.session() as session:
        binding = participant_service.get_binding(
            session, channel=ChannelType.VK, external_user_id=str(vk_user_id)
        )
        if binding is not None:
            binding.messages_allowed = allowed
            session.flush()
