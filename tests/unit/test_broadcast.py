"""Тесты рассылок (п.15, 20.1 ТЗ): получатели только с привязкой Telegram,
транзакционные уведомления не относятся к этому механизму (проверяется в M9),
статусы DRAFT->SENDING->SENT/FAILED, статистика."""

from __future__ import annotations

from app.core.db import Database
from app.models.base import utcnow
from app.models.channel_binding import ChannelBinding
from app.models.enums import BroadcastStatus, ChannelType, TicketSource
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.ticket import Ticket
from app.models.ticket_pool import TicketPool
from app.services import broadcast_service as svc
from sqlalchemy import select
from sqlalchemy.orm import Session


def make_participant_with_channel(
    session: Session, phone: str, channel: ChannelType | None, external_id: str | None = None
) -> Participant:
    p = Participant(phone=phone, phone_verified=channel is not None)
    session.add(p)
    session.flush()
    if channel is not None:
        session.add(
            ChannelBinding(
                participant_id=p.id,
                channel=channel,
                external_user_id=external_id or f"ext-{p.id}",
                phone_verified=True,
                linked_at=utcnow(),
            )
        )
        session.flush()
    return p


def test_resolve_audience_only_telegram_bound(session: Session) -> None:
    p_tg = make_participant_with_channel(session, "79990000001", ChannelType.TELEGRAM)
    make_participant_with_channel(session, "79990000002", ChannelType.VK)
    make_participant_with_channel(session, "79990000003", None)  # без привязки вовсе

    result = svc.resolve_audience(session, {"segment": "all"})
    ids = {p.id for p in result}
    assert ids == {p_tg.id}


def test_resolve_audience_paid_segment(session: Session) -> None:
    giveaway = Giveaway(name="G", prefix="BRD", ticket_price=1000, max_tickets=10)
    session.add(giveaway)
    session.flush()

    paid = make_participant_with_channel(session, "79991110000", ChannelType.TELEGRAM, "tg-paid")
    unpaid = make_participant_with_channel(
        session, "79992220000", ChannelType.TELEGRAM, "tg-unpaid"
    )

    pool_row = TicketPool(giveaway_id=giveaway.id, number=1, shuffle_order=1, status="issued")
    session.add(pool_row)
    session.flush()
    session.add(
        Ticket(
            giveaway_id=giveaway.id,
            pool_id=pool_row.id,
            number=1,
            full_code="BRD-000001",
            participant_id=paid.id,
            source=TicketSource.ONLINE,
        )
    )
    session.flush()

    paid_result = svc.resolve_audience(session, {"segment": "paid"})
    assert {p.id for p in paid_result} == {paid.id}

    unpaid_result = svc.resolve_audience(session, {"segment": "unpaid"})
    assert {p.id for p in unpaid_result} == {unpaid.id}


def test_send_broadcast_updates_status_and_stats(db: Database) -> None:
    with db.session() as session:
        make_participant_with_channel(session, "79993330000", ChannelType.TELEGRAM, "tg-a")
        make_participant_with_channel(session, "79994440000", ChannelType.TELEGRAM, "tg-b")
        broadcast = svc.create_broadcast(session, title="Тест", message_text="Привет!")
        broadcast_id = broadcast.id

    sent_to: list[str] = []

    def fake_send(external_user_id: str, text: str) -> bool:
        sent_to.append(external_user_id)
        return external_user_id != "tg-b"  # имитируем одну ошибку доставки

    result = svc.send_broadcast(db, broadcast_id=broadcast_id, send_fn=fake_send)
    assert result.recipients == 2
    assert result.delivered == 1
    assert result.errors == 1
    assert set(sent_to) == {"tg-a", "tg-b"}

    with db.session() as session:
        from app.models.broadcast import Broadcast

        broadcast = session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        ).scalar_one()
        assert broadcast.status == BroadcastStatus.SENT
        assert broadcast.stats == {"recipients": 2, "delivered": 1, "errors": 1}
        assert broadcast.sent_at is not None


def test_send_broadcast_no_recipients_still_completes(db: Database) -> None:
    with db.session() as session:
        broadcast = svc.create_broadcast(session, title="Empty", message_text="Никто не получит")
        broadcast_id = broadcast.id

    result = svc.send_broadcast(db, broadcast_id=broadcast_id, send_fn=lambda ext, text: True)
    assert result.recipients == 0
    assert result.delivered == 0
