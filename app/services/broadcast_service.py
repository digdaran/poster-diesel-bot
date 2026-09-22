"""Сервис рассылок (п.15, 6.2 ТЗ). Рассылки — ТОЛЬКО через Telegram (продуктовое
решение): получатели — участники с привязкой канала `telegram`, остальные не
попадают в выборку. Транзакционные уведомления (постер, коды) сюда не относятся —
они идут через `app.channels.*` в канал покупки (см. ARCHITECTURE.md).

Отправка использует ту же инфраструктуру гарантированной доставки, что и
проактивные уведомления (`app/services/channel_delivery_queue.py`,
DECISIONS_LOG.md №72/№73/№79): немедленная попытка с backoff-ретраем на
получателя, а при неудаче — не потеря, а постановка в очередь на докрутку
фоновым циклом backend до успеха.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import Database
from app.models.base import utcnow
from app.models.broadcast import Broadcast
from app.models.channel_binding import ChannelBinding
from app.models.enums import BroadcastStatus, ChannelType, PendingDeliveryKind, TicketSource
from app.models.participant import Participant
from app.models.ticket import Ticket
from app.services import channel_delivery_queue
from app.services.channel_delivery_queue import DeliverableChannel

logger = structlog.get_logger(__name__)


class BroadcastNotFoundError(Exception):
    pass


class BroadcastNotDraftError(Exception):
    pass


def create_broadcast(
    session: Session,
    *,
    title: str,
    message_text: str,
    audience_filter: dict[str, Any] | None = None,
) -> Broadcast:
    broadcast = Broadcast(
        title=title,
        message_text=message_text,
        audience_filter=audience_filter or {"segment": "all"},
    )
    session.add(broadcast)
    session.flush()
    return broadcast


def _tickets_subquery_filter(giveaway_id: int | None):  # type: ignore[no-untyped-def]
    stmt = select(Ticket.participant_id)
    if giveaway_id is not None:
        stmt = stmt.where(Ticket.giveaway_id == giveaway_id)
    return stmt


def resolve_audience(session: Session, audience_filter: dict[str, Any]) -> list[Participant]:
    """Строит список получателей по критериям (п.15 ТЗ). Всегда ограничивает выборку
    участниками с привязкой Telegram — рассылки в других каналах не поддерживаются.
    """
    segment = audience_filter.get("segment", "all")
    giveaway_id = audience_filter.get("giveaway_id")
    registered_after = audience_filter.get("registered_after")
    registered_before = audience_filter.get("registered_before")
    min_tickets = audience_filter.get("min_tickets")

    stmt = (
        select(Participant)
        .join(ChannelBinding, ChannelBinding.participant_id == Participant.id)
        .where(ChannelBinding.channel == ChannelType.TELEGRAM)
        .distinct()
    )

    if registered_after:
        stmt = stmt.where(Participant.created_at >= registered_after)
    if registered_before:
        stmt = stmt.where(Participant.created_at <= registered_before)

    ticket_query = _tickets_subquery_filter(giveaway_id)

    if segment == "paid":
        stmt = stmt.where(Participant.id.in_(ticket_query))
    elif segment == "unpaid":
        stmt = stmt.where(Participant.id.not_in(ticket_query))
    elif segment == "offline":
        stmt = stmt.where(
            Participant.id.in_(ticket_query.where(Ticket.source == TicketSource.MANUAL))
        )
    elif segment == "online":
        stmt = stmt.where(
            Participant.id.in_(ticket_query.where(Ticket.source == TicketSource.ONLINE))
        )
    elif giveaway_id is not None:
        # segment == "all", но сужено конкретным розыгрышем. РАНЬШЕ giveaway_id
        # в этой ветке молча игнорировался целиком (баг, найден на проде —
        # рассылка с фильтром {"segment": "all", "giveaway_id": <любой>} била
        # по ВСЕМ Telegram-привязанным участникам вместо ожидаемой пустой/узкой
        # аудитории). `Participant` не связан с `Giveaway` иначе как через
        # `Ticket` — единственная содержательная трактовка "все участники
        # этого розыгрыша" здесь совпадает с "paid", ограниченным giveaway_id
        # (`ticket_query` уже учитывает `giveaway_id`, см. выше).
        stmt = stmt.where(Participant.id.in_(ticket_query))
    # segment == "all" без giveaway_id -> без доп. фильтра по покупкам вовсе

    participants = list(session.execute(stmt).scalars())

    if min_tickets:
        filtered = []
        for p in participants:
            count_stmt = select(Ticket).where(Ticket.participant_id == p.id)
            if giveaway_id is not None:
                count_stmt = count_stmt.where(Ticket.giveaway_id == giveaway_id)
            count = len(list(session.execute(count_stmt).scalars()))
            if count >= min_tickets:
                filtered.append(p)
        participants = filtered

    return participants


@dataclass(frozen=True)
class SendResult:
    recipients: int
    delivered: int
    queued: int
    """Не ушло с первой попытки, но НЕ потеряно — поставлено в очередь
    гарантированной доставки (`channel_delivery_queue`), докрутится фоновым
    циклом. Эта финальная статистика — снимок на момент отправки, "queued"
    получатели впоследствии станут доставленными без обновления этого снимка."""
    errors: int
    """Получателя не удалось даже поставить в очередь (сбой самой БД при
    INSERT) — на практике почти никогда не встречается."""


def mark_broadcast_sending(db: Database, *, broadcast_id: int) -> Broadcast:
    """Быстрый синхронный переход DRAFT -> SENDING. Вызывается прямо в HTTP-
    обработчике перед тем, как реальная отправка (`send_broadcast`, может
    занимать секунды-минуты для большой аудитории) уйдёт в фоновую задачу —
    HTTP-ответ не должен блокироваться на всё это время."""
    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        if broadcast is None:
            raise BroadcastNotFoundError(f"Рассылка {broadcast_id} не найдена")
        if broadcast.status != BroadcastStatus.DRAFT:
            raise BroadcastNotDraftError(
                "Рассылку можно отправить только из статуса «черновик» "
                f"(сейчас {broadcast.status.value})"
            )
        broadcast.status = BroadcastStatus.SENDING
        session.flush()
        session.expunge(broadcast)
        return broadcast


async def send_broadcast(
    db: Database, *, broadcast_id: int, telegram_channel: DeliverableChannel | None
) -> SendResult:
    """Реальная отправка — предполагает, что статус уже SENDING
    (`mark_broadcast_sending` вызывается заранее). Каждому получателю —
    независимая попытка через `channel_delivery_queue.send_or_enqueue`:
    доставлено сейчас (`delivered`) либо поставлено в очередь на
    гарантированную докрутку (`queued`) — оба исхода НЕ являются провалом
    рассылки в целом; `errors` — только для сбоя самой постановки в очередь.
    `telegram_channel=None` (TELEGRAM_BOT_TOKEN не задан в этом процессе) —
    все получатели ставятся в очередь напрямую, докрутятся, когда канал
    появится."""
    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        assert broadcast is not None
        participants = resolve_audience(session, broadcast.audience_filter)
        recipients: list[tuple[int, str]] = [
            (p.id, binding.external_user_id)
            for p in participants
            for binding in p.channel_bindings
            if binding.channel == ChannelType.TELEGRAM
        ]
        message_text = broadcast.message_text

    results = await asyncio.gather(
        *(
            channel_delivery_queue.send_or_enqueue(
                db,
                channel=telegram_channel,
                channel_type=ChannelType.TELEGRAM,
                external_user_id=external_user_id,
                kind=PendingDeliveryKind.TEXT_MESSAGE,
                payload={"text": message_text},
                participant_id=participant_id,
            )
            for participant_id, external_user_id in recipients
        ),
        return_exceptions=True,
    )

    delivered = 0
    queued = 0
    errors = 0
    for (participant_id, _external_user_id), result in zip(recipients, results, strict=True):
        if isinstance(result, BaseException):
            errors += 1
            logger.error(
                "broadcast_recipient_send_or_enqueue_failed",
                broadcast_id=broadcast_id,
                participant_id=participant_id,
                error=str(result),
            )
        elif result:
            delivered += 1
        else:
            queued += 1

    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        assert broadcast is not None
        broadcast.status = (
            BroadcastStatus.SENT
            if not recipients or delivered > 0 or queued > 0
            else BroadcastStatus.FAILED
        )
        broadcast.stats = {
            "recipients": len(recipients),
            "delivered": delivered,
            "queued": queued,
            "errors": errors,
        }
        broadcast.sent_at = utcnow()
        session.flush()

    return SendResult(recipients=len(recipients), delivered=delivered, queued=queued, errors=errors)
