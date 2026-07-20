"""Тесты проактивных уведомлений об исходе платежа (см. app/services/notification_service.py,
DECISIONS.md): успех доставляет постер+коды через привязку Telegram участника,
отказ шлёт короткое уведомление, отсутствие привязки — тихий no-op."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.db import Database
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.payments.mock import MockProvider
from app.services import notification_service
from app.services import payment_service as svc
from app.services import ticket_pool_service as pool_svc


@dataclass
class FakeTelegramChannel:
    deliver_purchase_calls: list[dict] = field(default_factory=list)
    send_message_calls: list[tuple[str, str]] = field(default_factory=list)

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None:
        self.deliver_purchase_calls.append(
            {
                "external_user_id": external_user_id,
                "poster_path": poster_path,
                "codes": codes,
                "intro": intro,
            }
        )

    async def send_message(self, external_user_id: str, text: str, **kwargs: object) -> None:
        self.send_message_calls.append((external_user_id, text))


def make_giveaway(db: Database, *, max_tickets: int = 10, prefix: str = "NTF") -> int:
    with db.session() as session:
        g = Giveaway(name="Test", prefix=prefix, ticket_price=10000, max_tickets=max_tickets)
        session.add(g)
        session.flush()
        pool_svc.open_registration(session, g)
        return g.id


def make_participant_with_binding(db: Database, *, phone: str = "79991234567") -> int:
    with db.session() as session:
        p = Participant(phone=phone, phone_verified=True)
        session.add(p)
        session.flush()
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=ChannelType.TELEGRAM,
                external_user_id="123456",
                phone_verified=True,
            )
        )
        session.flush()
        return p.id


def make_participant_without_binding(db: Database, *, phone: str = "79997654321") -> int:
    with db.session() as session:
        p = Participant(phone=phone, phone_verified=False)
        session.add(p)
        session.flush()
        return p.id


async def test_notify_success_delivers_purchase_via_binding(db: Database) -> None:
    gid = make_giveaway(db)
    pid = make_participant_with_binding(db)
    outcome = svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok
    finalize = svc.finalize_payment(
        db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize.applied

    channel = FakeTelegramChannel()
    await notification_service.notify_payment_outcome(db, channel, finalize)

    assert len(channel.deliver_purchase_calls) == 1
    call = channel.deliver_purchase_calls[0]
    assert call["external_user_id"] == "123456"
    assert len(call["codes"]) == 2
    assert not channel.send_message_calls


async def test_notify_failure_sends_short_message(db: Database) -> None:
    gid = make_giveaway(db)
    pid = make_participant_with_binding(db)
    outcome = svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok
    finalize = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.FAILED)
    assert finalize.applied

    channel = FakeTelegramChannel()
    await notification_service.notify_payment_outcome(db, channel, finalize)

    assert not channel.deliver_purchase_calls
    assert len(channel.send_message_calls) == 1
    assert channel.send_message_calls[0][0] == "123456"


async def test_notify_without_binding_is_noop(db: Database) -> None:
    gid = make_giveaway(db)
    pid = make_participant_without_binding(db)
    outcome = svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79997654321",
        quantity=1,
    )
    assert outcome.ok
    finalize = svc.finalize_payment(
        db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize.applied

    channel = FakeTelegramChannel()
    await notification_service.notify_payment_outcome(db, channel, finalize)

    assert not channel.deliver_purchase_calls
    assert not channel.send_message_calls


async def test_notify_noop_when_not_applied(db: Database) -> None:
    """`applied=False` означает повторный webhook/гонку с фоновой сверкой —
    уведомление уже было отправлено при первой финализации, повторно слать не нужно."""
    not_applied = svc.FinalizeOutcome(applied=False)
    channel = FakeTelegramChannel()
    await notification_service.notify_payment_outcome(db, channel, not_applied)
    assert not channel.deliver_purchase_calls
    assert not channel.send_message_calls
