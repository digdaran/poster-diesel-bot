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

# Кооперативный флаг "экстренной остановки" активной рассылки — по
# broadcast_id, в памяти ЭТОГО процесса (backend — единственный процесс,
# выполняющий send_broadcast в фоне, см. DECISIONS_LOG.md — тот же принцип
# "без кросс-процессной координации", что и у channel_delivery_queue).
# Проверяется не перед КАЖДОЙ отправкой сразу (все корутины рассылки
# создаются практически одновременно через asyncio.gather — проверка "в
# самом начале" корутины была бы бесполезна, т.к. отменить успели бы только
# ещё не созданные задачи), а сразу после того, как корутина реально
# получает своё "окно" на отправку (после захвата семафора внутри
# send_broadcast) — так получатели, чья очередь ещё не подошла, действительно
# не отправляются.
_cancel_requested: set[int] = set()


class BroadcastNotFoundError(Exception):
    pass


class BroadcastNotDraftError(Exception):
    pass


class BroadcastNotSendingError(Exception):
    """Остановить можно только активную (SENDING) рассылку."""


class BroadcastSendingInProgressError(Exception):
    """Удалить нельзя, пока рассылка активна (SENDING) — сначала остановить."""


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
    cancelled: int
    """Получатель не был даже затронут — рассылку остановили (см.
    `request_cancel_broadcast`) до того, как до него дошла очередь."""
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


def request_cancel_broadcast(db: Database, *, broadcast_id: int) -> Broadcast:
    """Экстренная остановка активной рассылки — выставляет кооперативный флаг,
    который проверяется внутри `send_broadcast` перед отправкой каждому
    следующему получателю (см. `_cancel_requested`). НЕ трогает
    `Broadcast.status` сама — финальный переход в `CANCELLED` (со статистикой,
    сколько реально успело уйти) делает сама `send_broadcast`, когда
    доработает до конца (обычно несколько секунд после вызова — уже начатые
    отправки, ограниченные `CHANNEL_SEND_CONCURRENCY_LIMIT`, доигрываются до
    конца, а не обрываются на середине сетевого запроса)."""
    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        if broadcast is None:
            raise BroadcastNotFoundError(f"Рассылка {broadcast_id} не найдена")
        if broadcast.status != BroadcastStatus.SENDING:
            raise BroadcastNotSendingError(
                "Остановить можно только активную рассылку " f"(сейчас {broadcast.status.value})"
            )
        session.expunge(broadcast)
    _cancel_requested.add(broadcast_id)
    return broadcast


def delete_broadcast(db: Database, *, broadcast_id: int) -> None:
    """Удаляет рассылку — запрещено, пока она активна (`SENDING`), чтобы не
    выбить БД-строку из-под ещё выполняющейся фоновой задачи (`send_broadcast`
    держит `broadcast_id` и ожидает найти строку по нему на финальном шаге).
    Сначала остановите рассылку (`request_cancel_broadcast`), затем удаляйте."""
    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        if broadcast is None:
            raise BroadcastNotFoundError(f"Рассылка {broadcast_id} не найдена")
        if broadcast.status == BroadcastStatus.SENDING:
            raise BroadcastSendingInProgressError(
                "Нельзя удалить рассылку, пока она отправляется — сначала остановите её"
            )
        session.delete(broadcast)


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
    появится.

    Экстренная остановка (`request_cancel_broadcast`) — кооперативная:
    `_send_semaphore` ниже (тот же размер, что `CHANNEL_SEND_CONCURRENCY_LIMIT`
    у самого канала) — единственная точка, где имеет смысл проверять флаг,
    т.к. ВСЕ корутины по получателям создаются практически одновременно
    (`asyncio.gather`) и проверка "в самом начале" корутины была бы
    бесполезна — почти все уже успели бы её пройти до того, как оператор
    физически успел бы нажать "Остановить". Проверка сразу после захвата
    семафора означает, что реально останавливаются только те получатели, чья
    очередь ЕЩЁ не подошла — уже начатые (до `CHANNEL_SEND_CONCURRENCY_LIMIT`
    штук) доигрывают до конца, не обрываются на середине сетевого запроса."""
    _cancel_requested.discard(broadcast_id)
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

    send_semaphore = asyncio.Semaphore(db.settings.channel_send_concurrency_limit)

    async def _send_one(participant_id: int, external_user_id: str) -> bool | None:
        async with send_semaphore:
            if broadcast_id in _cancel_requested:
                return None  # ещё не начато — остановлено до этого получателя
            return await channel_delivery_queue.send_or_enqueue(
                db,
                channel=telegram_channel,
                channel_type=ChannelType.TELEGRAM,
                external_user_id=external_user_id,
                kind=PendingDeliveryKind.TEXT_MESSAGE,
                payload={"text": message_text},
                participant_id=participant_id,
            )

    results = await asyncio.gather(
        *(
            _send_one(participant_id, external_user_id)
            for participant_id, external_user_id in recipients
        ),
        return_exceptions=True,
    )
    _cancel_requested.discard(broadcast_id)

    delivered = 0
    queued = 0
    cancelled = 0
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
        elif result is None:
            cancelled += 1
        elif result:
            delivered += 1
        else:
            queued += 1

    with db.session() as session:
        broadcast = session.get(Broadcast, broadcast_id)
        assert broadcast is not None
        if cancelled > 0:
            broadcast.status = BroadcastStatus.CANCELLED
        elif not recipients or delivered > 0 or queued > 0:
            broadcast.status = BroadcastStatus.SENT
        else:
            broadcast.status = BroadcastStatus.FAILED
        broadcast.stats = {
            "recipients": len(recipients),
            "delivered": delivered,
            "queued": queued,
            "cancelled": cancelled,
            "errors": errors,
        }
        broadcast.sent_at = utcnow()
        session.flush()

    return SendResult(
        recipients=len(recipients),
        delivered=delivered,
        queued=queued,
        cancelled=cancelled,
        errors=errors,
    )
