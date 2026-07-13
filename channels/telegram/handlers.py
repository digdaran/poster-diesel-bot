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

from channels.telegram.state import PurchaseStates, get_active_provider, get_channel_db

if TYPE_CHECKING:
    from channels.telegram.channel import TelegramChannel

logger = structlog.get_logger(__name__)
router = Router(name="telegram-main")

_MAIN_KEYBOARD_BUTTONS = [["🎟 Купить номерки", "📋 Мои номерки"], ["ℹ️ Помощь"]]

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
    await state.clear()
    channel = _get_channel()
    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id=_uid(message)
        )
    greeting = "Добро пожаловать в бот розыгрышей цифровых постеров!"
    if participant is None:
        greeting += (
            "\n\nПоделитесь контактом, чтобы подтвердить номер и получить доступ к своим покупкам."
        )
    keyboard = channel.render_keyboard(_MAIN_KEYBOARD_BUTTONS)
    await message.answer(greeting, reply_markup=keyboard)
    if participant is None:
        await channel.request_contact(_uid(message))


@router.message(F.contact)
async def on_contact(message: Message) -> None:
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

    await message.answer("Номер подтверждён! Теперь вам доступна история покупок и номерков.")


@router.message(F.text == "🎟 Купить номерки")
async def on_buy(message: Message, state: FSMContext) -> None:
    db = get_channel_db()
    with db.session() as session:
        giveaways = list(
            session.execute(
                select(Giveaway).where(
                    Giveaway.is_registration_open.is_(True), Giveaway.is_locked.is_(False)
                )
            ).scalars()
        )
        giveaways = [g for g in giveaways if g.free_tickets_count > 0]

    if not giveaways:
        await message.answer("Сейчас нет доступных для покупки розыгрышей.")
        return

    if len(giveaways) == 1:
        await _prompt_quantity(message, state, giveaways[0])
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [
            InlineKeyboardButton(
                text=f"{g.name} ({g.free_tickets_count} своб.)", callback_data=f"giveaway:{g.id}"
            )
        ]
        for g in giveaways
    ]
    await message.answer(
        "Выберите розыгрыш:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await state.set_state(PurchaseStates.choosing_giveaway)


async def _prompt_quantity(message: Message, state: FSMContext, giveaway: Giveaway) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    max_qty = min(5, giveaway.free_tickets_count)
    rows = [
        [
            InlineKeyboardButton(text=str(q), callback_data=f"qty:{giveaway.id}:{q}")
            for q in range(1, max_qty + 1)
        ]
    ]
    await message.answer(
        f"«{giveaway.name}»: цена номерка {giveaway.ticket_price / 100:.2f} ₽. "
        f"Сколько номерков хотите приобрести? (доступно {giveaway.free_tickets_count})",
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
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    await _prompt_quantity(_msg(callback), state, giveaway)
    await callback.answer()


@router.callback_query(F.data.startswith("qty:"))
async def on_quantity_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    assert callback.data is not None
    _, giveaway_id_raw, qty_raw = callback.data.split(":")
    giveaway_id, quantity = int(giveaway_id_raw), int(qty_raw)

    db = get_channel_db()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id=_uid(callback)
        )

    if participant is not None:
        await _create_and_offer_payment(_msg(callback), giveaway_id, quantity, participant.id)
        await state.clear()
        await callback.answer()
        return

    # Подарочная покупка на неподтверждённый номер (п.7.1, 10.3, 10.5 ТЗ): просим
    # ввести номер получателя вручную — своей учётки у покупателя ещё нет.
    await state.update_data(giveaway_id=giveaway_id, quantity=quantity)
    await state.set_state(PurchaseStates.awaiting_phone_for_gift)
    await _msg(callback).answer(
        "Введите номер телефона получателя номерков (формат: +7XXXXXXXXXX). "
        "Постер и коды придут в этот чат."
    )
    await callback.answer()


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

    await _create_and_offer_payment(message, giveaway_id, quantity, participant_id)
    await state.clear()


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

    outcome = payment_svc.create_payment_safe(
        db,
        provider,
        giveaway_id=giveaway_id,
        participant_id=participant_id,
        participant_phone=phone,
        quantity=quantity,
    )
    if not outcome.ok:
        await message.answer(
            f"К сожалению, свободных номерков меньше, чем нужно (доступно {outcome.free_count}). "
            "Попробуйте выбрать количество заново."
        )
        return

    assert outcome.created is not None
    keyboard = channel.render_payment_prompt(
        payment_url=outcome.created.payment_url, qr_code_payload=outcome.created.qr_code_payload
    )
    await message.answer(
        f"Счёт создан на {quantity} номерок(ов). Оплатите по ссылке ниже или отсканируйте QR "
        "в приложении банка (СБП).",
        reply_markup=keyboard,
    )
    if outcome.created.qr_code_payload:
        await channel.send_qr_code(str(message.chat.id), outcome.created.qr_code_payload)


@router.callback_query(F.data == "check_payment")
async def on_check_payment(callback: CallbackQuery) -> None:
    """Резервная проверка оплаты (п.7.5, 8.1, 10.2 ТЗ)."""
    db = get_channel_db()
    provider = get_active_provider(db)

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

    bank_status = provider.check_status(payment.order_id)
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
    codes = "\n".join(t.full_code for t in (outcome.tickets or []))
    text = f"Оплата прошла успешно! Ваши номерки:\n{codes}"
    if giveaway and giveaway.digital_poster_path:
        await channel.send_media(str(message.chat.id), giveaway.digital_poster_path, caption=text)
    else:
        await message.answer(text)


@router.message(F.text == "📋 Мои номерки")
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
            await message.answer(
                "Доступ к истории номерков есть только после подтверждения номера. "
                "Поделитесь контактом, чтобы получить доступ."
            )
            return
        tickets = list(
            session.execute(
                select(Ticket).where(Ticket.participant_id == binding.participant_id)
            ).scalars()
        )

    if not tickets:
        await message.answer("У вас пока нет номерков.")
        return
    codes = "\n".join(t.full_code for t in tickets)
    await message.answer(f"Ваши номерки ({len(tickets)}):\n{codes}")


@router.message(F.text == "ℹ️ Помощь")
@router.message(Command("help"))
async def on_help(message: Message) -> None:
    db = get_channel_db()
    with db.session() as session:
        platform_settings = settings_service.get_or_create_settings(session)
    contacts = platform_settings.support_contacts or {}
    lines = [
        "Справка по боту:",
        "— «Купить номерки» — приобрести номерки в активном розыгрыше.",
        "— «Мои номерки» — история покупок (после подтверждения номера).",
    ]
    if contacts:
        lines.append("\nПоддержка:")
        for key, value in contacts.items():
            lines.append(f"{key}: {value}")
    await message.answer("\n".join(lines))
