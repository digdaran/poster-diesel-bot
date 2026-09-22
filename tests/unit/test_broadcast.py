"""Тесты рассылок (п.15, 20.1 ТЗ): получатели только с привязкой Telegram,
транзакционные уведомления не относятся к этому механизму, статусы
DRAFT->SENDING->SENT/FAILED, статистика. Реальная отправка — через ту же
гарантированную доставку, что и проактивные уведомления (DECISIONS_LOG.md
№79) — неудавшийся получатель не теряется, а ставится в очередь
(`pending_channel_deliveries`), что покрыто отдельно."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
from app.core.db import Database
from app.models.base import utcnow
from app.models.broadcast import Broadcast
from app.models.channel_binding import ChannelBinding
from app.models.enums import BroadcastStatus, ChannelType, PendingDeliveryKind, TicketSource
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.pending_channel_delivery import PendingChannelDelivery
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


def test_resolve_audience_all_segment_with_giveaway_id_scopes_to_that_giveaway(
    session: Session,
) -> None:
    """Регрессия на инцидент на проде: `{"segment": "all", "giveaway_id": N}`
    раньше игнорировал `giveaway_id` целиком в ветке "all" и возвращал ВСЕХ
    Telegram-привязанных участников независимо от покупок — рассылка,
    задуманная как узкая/пустая, ушла всем 3274 подписчикам бота. `Participant`
    не связан с `Giveaway` иначе как через `Ticket`, поэтому "все участники
    ЭТОГО розыгрыша" может значить только "купившие хотя бы один номерок этого
    розыгрыша" — то же множество, что и "paid", ограниченный giveaway_id."""
    giveaway = Giveaway(name="G", prefix="ALL", ticket_price=1000, max_tickets=10)
    other_giveaway = Giveaway(name="Other", prefix="OTH", ticket_price=1000, max_tickets=10)
    session.add_all([giveaway, other_giveaway])
    session.flush()

    buyer = make_participant_with_channel(session, "79993000000", ChannelType.TELEGRAM, "tg-buyer")
    other_buyer = make_participant_with_channel(
        session, "79994000000", ChannelType.TELEGRAM, "tg-other-buyer"
    )
    make_participant_with_channel(session, "79995000000", ChannelType.TELEGRAM, "tg-no-purchase")

    pool_row = TicketPool(giveaway_id=giveaway.id, number=1, shuffle_order=1, status="issued")
    other_pool_row = TicketPool(
        giveaway_id=other_giveaway.id, number=1, shuffle_order=1, status="issued"
    )
    session.add_all([pool_row, other_pool_row])
    session.flush()
    session.add(
        Ticket(
            giveaway_id=giveaway.id,
            pool_id=pool_row.id,
            number=1,
            full_code="ALL-000001",
            participant_id=buyer.id,
            source=TicketSource.ONLINE,
        )
    )
    session.add(
        Ticket(
            giveaway_id=other_giveaway.id,
            pool_id=other_pool_row.id,
            number=1,
            full_code="OTH-000001",
            participant_id=other_buyer.id,
            source=TicketSource.ONLINE,
        )
    )
    session.flush()

    result = svc.resolve_audience(session, {"segment": "all", "giveaway_id": giveaway.id})
    assert {p.id for p in result} == {buyer.id}


def test_resolve_audience_all_segment_with_unknown_giveaway_id_is_empty(session: Session) -> None:
    """Тот самый сценарий, который на проде по ошибке разослал сообщение всем
    3274 подписчикам вместо пустой аудитории — несуществующий `giveaway_id`
    теперь корректно даёт пустой список, а не игнорируется."""
    make_participant_with_channel(session, "79996000000", ChannelType.TELEGRAM, "tg-someone")

    result = svc.resolve_audience(session, {"segment": "all", "giveaway_id": 999999999})
    assert result == []


def test_resolve_audience_all_segment_without_giveaway_id_is_unrestricted(
    session: Session,
) -> None:
    """Без `giveaway_id` поведение "all" не меняется — все Telegram-привязанные
    участники независимо от покупок (иначе это была бы уже другая семантика)."""
    p1 = make_participant_with_channel(session, "79997000000", ChannelType.TELEGRAM, "tg-1")
    p2 = make_participant_with_channel(session, "79998000000", ChannelType.TELEGRAM, "tg-2")

    result = svc.resolve_audience(session, {"segment": "all"})
    assert {p.id for p in result} == {p1.id, p2.id}


@dataclass
class FakeTelegramChannel:
    """Реализует только `send_message` — рассылки шлют исключительно текст,
    остальные методы `DeliverableChannel` (QR/постер) им не нужны."""

    fail_for: set[str] = field(default_factory=set)
    sent_to: list[str] = field(default_factory=list)

    async def send_message(self, external_user_id: str, text: str, **kwargs: object) -> None:
        self.sent_to.append(external_user_id)
        if external_user_id in self.fail_for:
            raise RuntimeError("messages.send failed")


async def test_send_broadcast_updates_status_and_stats(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Один получатель падает на всех попытках send_with_retry — не теряется,
    а ставится в очередь гарантированной доставки (`queued`, не `errors`)."""
    monkeypatch.setattr("app.channels.retry.asyncio.sleep", AsyncMock())
    with db.session() as session:
        make_participant_with_channel(session, "79993330000", ChannelType.TELEGRAM, "tg-a")
        make_participant_with_channel(session, "79994440000", ChannelType.TELEGRAM, "tg-b")
        broadcast = svc.create_broadcast(session, title="Тест", message_text="Привет!")
        broadcast_id = broadcast.id

    channel = FakeTelegramChannel(fail_for={"tg-b"})
    result = await svc.send_broadcast(db, broadcast_id=broadcast_id, telegram_channel=channel)
    assert result.recipients == 2
    assert result.delivered == 1
    assert result.queued == 1
    assert result.errors == 0
    assert set(channel.sent_to) == {"tg-a", "tg-b"}

    with db.session() as session:
        broadcast = session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        ).scalar_one()
        assert broadcast.status == BroadcastStatus.SENT
        assert broadcast.stats == {"recipients": 2, "delivered": 1, "queued": 1, "errors": 0}
        assert broadcast.sent_at is not None

    with db.session() as session:
        rows = list(session.execute(select(PendingChannelDelivery)).scalars())
    assert len(rows) == 1
    assert rows[0].external_user_id == "tg-b"
    assert rows[0].channel == ChannelType.TELEGRAM
    assert rows[0].kind == PendingDeliveryKind.TEXT_MESSAGE
    assert rows[0].payload == {"text": "Привет!"}


async def test_send_broadcast_no_recipients_still_completes(db: Database) -> None:
    with db.session() as session:
        broadcast = svc.create_broadcast(session, title="Empty", message_text="Никто не получит")
        broadcast_id = broadcast.id

    result = await svc.send_broadcast(db, broadcast_id=broadcast_id, telegram_channel=None)
    assert result.recipients == 0
    assert result.delivered == 0
    assert result.queued == 0
    assert result.errors == 0


async def test_send_broadcast_without_channel_queues_all_recipients(db: Database) -> None:
    """backend поднят без TELEGRAM_BOT_TOKEN (dev/тест-окружение) — сообщения
    не теряются, а ставятся в очередь напрямую (без попытки), докрутятся, когда
    канал появится (см. channel_delivery_queue.send_or_enqueue, channel=None)."""
    with db.session() as session:
        make_participant_with_channel(session, "79995550000", ChannelType.TELEGRAM, "tg-c")
        broadcast = svc.create_broadcast(session, title="NoChannel", message_text="Привет")
        broadcast_id = broadcast.id

    result = await svc.send_broadcast(db, broadcast_id=broadcast_id, telegram_channel=None)
    assert result.recipients == 1
    assert result.delivered == 0
    assert result.queued == 1
    assert result.errors == 0


def test_mark_broadcast_sending_transitions_from_draft(db: Database) -> None:
    with db.session() as session:
        broadcast = svc.create_broadcast(session, title="X", message_text="Y")
        broadcast_id = broadcast.id

    result = svc.mark_broadcast_sending(db, broadcast_id=broadcast_id)
    assert result.status == BroadcastStatus.SENDING


def test_mark_broadcast_sending_rejects_non_draft(db: Database) -> None:
    with db.session() as session:
        broadcast = svc.create_broadcast(session, title="X", message_text="Y")
        broadcast_id = broadcast.id
    svc.mark_broadcast_sending(db, broadcast_id=broadcast_id)

    with pytest.raises(svc.BroadcastNotDraftError):
        svc.mark_broadcast_sending(db, broadcast_id=broadcast_id)


def test_mark_broadcast_sending_raises_for_unknown_id(db: Database) -> None:
    with pytest.raises(svc.BroadcastNotFoundError):
        svc.mark_broadcast_sending(db, broadcast_id=999999)
