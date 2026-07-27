"""Тесты раздела «Мои покупки»: отображение PENDING-платежей со статусом
квитанции (`payment_service.list_pending_payments`/`get_own_pending_payment`) и
рендеринг списка в Telegram-хендлере `on_my_tickets` (см. DECISIONS_LOG.md №49).
VK-хендлер бизнес-логику полностью дублирует (см. ARCHITECTURE.md §7.1) и
отдельными юнит-тестами в этом репозитории не покрыт — см. соглашение в
tests/unit/test_telegram_navigation.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.db import Database
from app.models.enums import ChannelType, PaymentProviderType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.payment_receipt import PaymentReceipt
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


def test_list_pending_payments_reports_receipt_status_and_invoice(db: Database) -> None:
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

    result = payment_svc.list_pending_payments(db, participant_id=participant_id)

    by_id = {p.payment_id: p for p in result}
    assert by_id[with_receipt].has_receipt is True
    assert by_id[without_receipt].has_receipt is False
    assert by_id[without_receipt].amount == 20000
    assert by_id[without_receipt].quantity == 2
    assert by_id[without_receipt].invoice_no is not None
    # Самые новые счета — первыми.
    assert [p.payment_id for p in result] == [without_receipt, with_receipt]


def test_list_pending_payments_excludes_other_participants_and_non_pending(
    db: Database,
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

    result = payment_svc.list_pending_payments(db, participant_id=participant_id)

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
    await handlers_module.on_my_tickets(message)  # type: ignore[arg-type]

    message.answer.assert_awaited_once()
    text, kwargs = message.answer.await_args.args, message.answer.await_args.kwargs
    assert "Неоплаченные счета" in text[0]
    keyboard = kwargs["reply_markup"]
    assert keyboard is not None
    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    assert len(buttons) == 1
    assert buttons[0].callback_data == f"select_payment_receipt:{without_receipt}"
