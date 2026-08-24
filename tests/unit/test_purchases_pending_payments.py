"""Тесты раздела «Мои покупки»: отображение PENDING-платежей со статусом
квитанции (`payment_service.list_pending_payments`/`get_own_pending_payment`) и
рендеринг списка в Telegram-хендлере `on_my_tickets` (см. DECISIONS_LOG.md №49).
VK-хендлер бизнес-логику полностью дублирует (см. ARCHITECTURE.md §7.1) и
отдельными юнит-тестами в этом репозитории не покрыт — см. соглашение в
tests/unit/test_telegram_navigation.py."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.enums import ChannelType, PanelUserRole, PaymentProviderType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.payment_receipt import PaymentReceipt
from app.services import manual_registration_service as manual_reg_svc
from app.services import participant_service
from app.services import payment_service as payment_svc
from app.services import ticket_pool_service as pool_svc

from channels.telegram import handlers as handlers_module


def _make_giveaway(db: Database, *, prefix: str) -> int:
    with db.session() as session:
        giveaway = Giveaway(
            name=f"Постер {prefix}", prefix=prefix, ticket_price=10000, max_tickets=10
        )
        session.add(giveaway)
        session.flush()
        pool_svc.open_registration(session, giveaway)
        return giveaway.id


def _make_participant(db: Database, *, phone: str) -> int:
    with db.session() as session:
        participant = Participant(phone=phone, phone_verified=True)
        session.add(participant)
        session.flush()
        return participant.id


def _make_payment(
    db: Database,
    *,
    participant_id: int,
    giveaway_id: int,
    order_id: str,
    payment_number: int | None,
    status: PaymentStatus = PaymentStatus.PENDING,
    amount: int = 10000,
    quantity: int = 1,
) -> int:
    with db.session() as session:
        payment = Payment(
            order_id=order_id,
            participant_id=participant_id,
            giveaway_id=giveaway_id,
            provider=PaymentProviderType.REQUISITES_QR,
            amount=amount,
            quantity=quantity,
            payment_number=payment_number,
            status=status,
        )
        session.add(payment)
        session.flush()
        return payment.id


def _close_forever(db: Database, giveaway_id: int) -> None:
    """Имитирует `POST /giveaways/{id}/close-registration` — необратимо закрывает
    уже открытую регистрацию (в отличие от «ещё не открыта»)."""
    with db.session() as session:
        giveaway = session.get(Giveaway, giveaway_id)
        assert giveaway is not None
        giveaway.is_registration_open = False


def _archive(db: Database, giveaway_id: int) -> None:
    """Архивация требует уже закрытой навсегда регистрации (см. `archive_giveaway`)."""
    with db.session() as session:
        giveaway = session.get(Giveaway, giveaway_id)
        assert giveaway is not None
        assert (
            not giveaway.is_registration_open
        ), "архивация возможна только после close-registration"
        giveaway.is_archived = True


def test_list_pending_payments_excludes_closed_forever_giveaway(
    db: Database, settings: Settings
) -> None:
    """«Мои покупки» не должны показывать неоплаченный счёт по коллекции, регистрация
    в которой закрыта навсегда — по решению владельца продукта."""
    giveaway_id = _make_giveaway(db, prefix="CLF")
    participant_id = _make_participant(db, phone="79990000010")
    _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-clf",
        payment_number=1,
    )

    _close_forever(db, giveaway_id)

    assert payment_svc.list_pending_payments(db, settings, participant_id=participant_id) == []


def test_list_pending_payments_excludes_archived_giveaway(db: Database, settings: Settings) -> None:
    giveaway_id = _make_giveaway(db, prefix="ARC")
    participant_id = _make_participant(db, phone="79990000011")
    _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-arc",
        payment_number=1,
    )

    _close_forever(db, giveaway_id)
    _archive(db, giveaway_id)

    assert payment_svc.list_pending_payments(db, settings, participant_id=participant_id) == []


def test_list_participant_tickets_excludes_closed_forever_and_archived_giveaways(
    db: Database,
) -> None:
    open_giveaway_id = _make_giveaway(db, prefix="OPN")
    closed_giveaway_id = _make_giveaway(db, prefix="CLF2")
    archived_giveaway_id = _make_giveaway(db, prefix="ARC2")
    participant_id = _make_participant(db, phone="79990000012")
    with db.session() as session:
        operator = PanelUser(login="op-clf", password_hash="x", role=PanelUserRole.OPERATOR)
        session.add(operator)
        session.flush()
        operator_id = operator.id

    def _issue_one(giveaway_id: int) -> None:
        outcome = manual_reg_svc.create_manual_registration_safe(
            db,
            giveaway_id=giveaway_id,
            participant_id=participant_id,
            quantity=1,
            operator_id=operator_id,
            ttl_seconds=3600,
        )
        assert outcome.ok
        manual_reg_svc.confirm_manual_registration(
            db, manual_registration_id=outcome.manual_registration_id
        )

    _issue_one(open_giveaway_id)
    _issue_one(closed_giveaway_id)
    _issue_one(archived_giveaway_id)

    _close_forever(db, closed_giveaway_id)
    _close_forever(db, archived_giveaway_id)
    _archive(db, archived_giveaway_id)

    with db.session() as session:
        tickets = pool_svc.list_participant_tickets(session, participant_id=participant_id)

    assert {t.giveaway_id for t in tickets} == {open_giveaway_id}


async def test_on_my_tickets_excludes_ticket_codes_from_closed_forever_giveaway(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    channel = _FakeChannel()
    monkeypatch.setattr(handlers_module, "_channel", channel)

    open_giveaway_id = _make_giveaway(db, prefix="MYO")
    closed_giveaway_id = _make_giveaway(db, prefix="MYC")
    phone = "79990000013"
    with db.session() as session:
        participant = Participant(phone=phone, phone_verified=True)
        session.add(participant)
        session.flush()
        participant_service.confirm_channel_binding(
            session,
            channel=ChannelType.TELEGRAM,
            external_user_id="302",
            phone_raw=phone,
            username="tg_user",
        )
        participant_id = participant.id
        operator = PanelUser(login="op-my", password_hash="x", role=PanelUserRole.OPERATOR)
        session.add(operator)
        session.flush()
        operator_id = operator.id

    def _issue_one(giveaway_id: int) -> None:
        outcome = manual_reg_svc.create_manual_registration_safe(
            db,
            giveaway_id=giveaway_id,
            participant_id=participant_id,
            quantity=1,
            operator_id=operator_id,
            ttl_seconds=3600,
        )
        assert outcome.ok
        manual_reg_svc.confirm_manual_registration(
            db, manual_registration_id=outcome.manual_registration_id
        )

    _issue_one(open_giveaway_id)
    _issue_one(closed_giveaway_id)
    _close_forever(db, closed_giveaway_id)

    message = _FakeMessage(uid=302)
    state = _FakeState()
    await handlers_module.on_my_tickets(message, state)  # type: ignore[arg-type]

    channel.send_ticket_codes.assert_awaited_once()
    _, sent_codes = channel.send_ticket_codes.await_args.args
    assert len(sent_codes) == 1
    assert sent_codes[0].startswith("MYO-")


def test_list_pending_payments_reports_receipt_status_and_invoice(
    db: Database, settings: Settings
) -> None:
    giveaway_id = _make_giveaway(db, prefix="PND")
    participant_id = _make_participant(db, phone="79990000001")
    with_receipt = _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-a",
        payment_number=1,
    )
    without_receipt = _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-b",
        payment_number=2,
        amount=20000,
        quantity=2,
    )
    with db.session() as session:
        session.add(PaymentReceipt(payment_id=with_receipt, file_path="/tmp/r.jpg"))

    result = payment_svc.list_pending_payments(db, settings, participant_id=participant_id)

    by_id = {p.payment_id: p for p in result}
    assert by_id[with_receipt].has_receipt is True
    assert by_id[without_receipt].has_receipt is False
    assert by_id[without_receipt].amount == 20000
    assert by_id[without_receipt].quantity == 2
    assert by_id[without_receipt].invoice_no is not None
    # expires_at = created_at + TTL — считается даже для счёта с уже присланной
    # квитанцией: счёт остаётся PENDING (и подверженным TTL) до подтверждения
    # сверкой выписки, наличие квитанции на это не влияет.
    with db.session() as session:
        payment = session.get(Payment, without_receipt)
        assert payment is not None
        expected_expiry = payment.created_at + dt.timedelta(
            days=settings.requisites_invoice_ttl_days
        )
    assert by_id[without_receipt].expires_at == expected_expiry
    # Самые новые счета — первыми.
    assert [p.payment_id for p in result] == [without_receipt, with_receipt]


def test_list_pending_payments_excludes_other_participants_and_non_pending(
    db: Database, settings: Settings
) -> None:
    giveaway_id = _make_giveaway(db, prefix="EXC")
    participant_id = _make_participant(db, phone="79990000002")
    other_participant_id = _make_participant(db, phone="79990000003")

    _make_payment(
        db,
        participant_id=other_participant_id,
        giveaway_id=giveaway_id,
        order_id="order-other",
        payment_number=1,
    )
    _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-succeeded",
        payment_number=2,
        status=PaymentStatus.SUCCEEDED,
    )

    result = payment_svc.list_pending_payments(db, settings, participant_id=participant_id)

    assert result == []


def test_get_own_pending_payment_checks_ownership_and_status(db: Database) -> None:
    giveaway_id = _make_giveaway(db, prefix="OWN")
    participant_id = _make_participant(db, phone="79990000004")
    other_participant_id = _make_participant(db, phone="79990000005")
    payment_id = _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-own",
        payment_number=1,
    )
    succeeded_id = _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-own-2",
        payment_number=2,
        status=PaymentStatus.SUCCEEDED,
    )

    found = payment_svc.get_own_pending_payment(
        db, payment_id=payment_id, participant_id=participant_id
    )
    assert found is not None
    assert found.id == payment_id

    assert (
        payment_svc.get_own_pending_payment(
            db, payment_id=payment_id, participant_id=other_participant_id
        )
        is None
    )
    assert (
        payment_svc.get_own_pending_payment(
            db, payment_id=succeeded_id, participant_id=participant_id
        )
        is None
    )


class _FakeUser:
    def __init__(self, uid: int) -> None:
        self.id = uid
        self.username = "tg_user"


class _FakeMessage:
    def __init__(self, uid: int) -> None:
        self.from_user = _FakeUser(uid)
        self.chat = MagicMock(id=uid)
        self.answer = AsyncMock()


class _FakeChannel:
    def __init__(self) -> None:
        self.send_ticket_codes = AsyncMock()


class _FakeState:
    """Заглушка aiogram FSMContext — проверяем только вызов `clear()`, которым
    хендлеры главного меню молча сбрасывают незавершённый диалог покупки/
    регистрации (см. DECISIONS_LOG.md)."""

    def __init__(self) -> None:
        self.clear = AsyncMock()


async def test_on_my_tickets_lists_pending_payments_with_button_only_for_missing_receipt(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    monkeypatch.setattr(handlers_module, "_channel", _FakeChannel())

    giveaway_id = _make_giveaway(db, prefix="MYP")
    phone = "79990000006"
    with db.session() as session:
        participant = Participant(phone=phone, phone_verified=True)
        session.add(participant)
        session.flush()
        participant_service.confirm_channel_binding(
            session,
            channel=ChannelType.TELEGRAM,
            external_user_id="301",
            phone_raw=phone,
            username="tg_user",
        )
        participant_id = participant.id

    with_receipt = _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-x",
        payment_number=1,
    )
    without_receipt = _make_payment(
        db,
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        order_id="order-y",
        payment_number=2,
    )
    with db.session() as session:
        session.add(PaymentReceipt(payment_id=with_receipt, file_path="/tmp/r.jpg"))

    message = _FakeMessage(uid=301)
    state = _FakeState()
    await handlers_module.on_my_tickets(message, state)  # type: ignore[arg-type]

    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once()
    text, kwargs = message.answer.await_args.args, message.answer.await_args.kwargs
    assert "Неоплаченные счета" in text[0]
    keyboard = kwargs["reply_markup"]
    assert keyboard is not None
    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    assert len(buttons) == 1
    assert buttons[0].callback_data == f"select_payment_receipt:{without_receipt}"
