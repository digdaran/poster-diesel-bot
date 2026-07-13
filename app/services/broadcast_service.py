"""Сервис рассылок (п.15, 6.2 ТЗ). Рассылки — ТОЛЬКО через Telegram (продуктовое
решение): получатели — участники с привязкой канала `telegram`, остальные не
попадают в выборку. Транзакционные уведомления (постер, коды) сюда не относятся —
они идут через `app.channels.*` в канал покупки (см. ARCHITECTURE.md).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import Database
from app.models.base import utcnow
from app.models.broadcast import Broadcast
from app.models.channel_binding import ChannelBinding
from app.models.enums import BroadcastStatus, ChannelType, TicketSource
from app.models.participant import Participant
from app.models.ticket import Ticket

SendFn = Callable[[str, str], bool]
"""Функция отправки сообщения: (external_user_id, text) -> успех.
Реализуется каналом Telegram (см. app.channels / channels.telegram, M9);
здесь сервис от неё абстрагирован для тестируемости и разделения ответственности."""


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
    # segment == "all" -> без доп. фильтра по покупкам

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
    errors: int


def send_broadcast(
    db: Database, *, broadcast_id: int, send_fn: SendFn, rate_limit_delay_sec: float = 0.0
) -> SendResult:
    """Отправляет рассылку (DRAFT -> SENDING -> SENT/FAILED, п.15 ТЗ). Учитывает
    лимиты Telegram через `rate_limit_delay_sec` между сообщениями (п.15 ТЗ)."""
    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        assert broadcast is not None
        broadcast.status = BroadcastStatus.SENDING
        session.flush()
        participants = resolve_audience(session, broadcast.audience_filter)
        recipients: list[tuple[int, str]] = [
            (p.id, binding.external_user_id)
            for p in participants
            for binding in p.channel_bindings
            if binding.channel == ChannelType.TELEGRAM
        ]
        message_text = broadcast.message_text

    delivered = 0
    errors = 0
    for _participant_id, external_user_id in recipients:
        try:
            ok = send_fn(external_user_id, message_text)
        except (
            Exception
        ):  # noqa: BLE001 — сбой доставки одному получателю не должен прерывать рассылку
            ok = False
        if ok:
            delivered += 1
        else:
            errors += 1
        if rate_limit_delay_sec:
            time.sleep(rate_limit_delay_sec)

    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        assert broadcast is not None
        broadcast.status = (
            BroadcastStatus.SENT if delivered > 0 or not recipients else BroadcastStatus.FAILED
        )
        broadcast.stats = {"recipients": len(recipients), "delivered": delivered, "errors": errors}
        broadcast.sent_at = utcnow()
        session.flush()

    return SendResult(recipients=len(recipients), delivered=delivered, errors=errors)
