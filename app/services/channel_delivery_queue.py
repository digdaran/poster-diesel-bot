"""Очередь гарантированной доставки сообщений через мессенджер-каналы
(DECISIONS_LOG.md №73) — по прямому запросу заказчика: доставка должна быть
гарантированной для конкретно взятого канала связи (без кросс-процессной
координации, см. `app/channels/retry.py`/№72).

`send_or_enqueue` — единая точка входа для всех проактивных/полу-проактивных
отправок (QR при создании счёта — `channels/*/handlers.py`; проактивные
уведомления об исходе платежа — `app/services/notification_service.py`;
рассылки — `app/services/broadcast_service.py`, DECISIONS_LOG.md №79): сперва
пытается отправить прямо сейчас (`send_with_retry`, до 3 попыток с backoff в
течение нескольких секунд), а если и это не удалось — НЕ считает доставку
потерянной (кроме заведомо постоянных сбоев, см. ниже), а кладёт
`PendingChannelDelivery` со status=PENDING и возвращает управление вызывающему
немедленно (без исключения). Дальше доставку докручивает фоновый цикл backend
(`process_pending_deliveries`, вызывается из `backend/background/__init__.py`)
на каждом тике, пока не пройдёт — без ограничения числа попыток.

Исключение — ПОСТОЯННЫЕ сбои (`_is_permanent_failure`: получатель заблокировал
бота/аккаунт удалён — Telegram, либо запретил сообщения — VK, см.
DECISIONS_LOG.md №82). Найдено на первой боевой рассылке: 103 из 3275
получателей были бы обречены на бесконечный цикл бесполезных повторов раз в
тик — такие сразу помечаются терминальным `UNDELIVERABLE`, не `PENDING`."""

from __future__ import annotations

import enum
from typing import Any, Protocol

import structlog
from sqlalchemy import select

from app.channels.retry import send_with_retry
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import ChannelType, PendingDeliveryKind, PendingDeliveryStatus
from app.models.pending_channel_delivery import PendingChannelDelivery

# Этот модуль используется во всех трёх процессах (backend, channel-telegram,
# channel-vk), но у каждого свой набор зависимостей (`docker/*.Dockerfile`,
# см. DECISIONS_LOG.md №71/№79): channel-telegram не ставит vkbottle,
# channel-vk не ставит aiogram — безусловный импорт сломал бы соответствующий
# процесс на старте. Оба нужны только для классификации "постоянных" ошибок
# (`_is_permanent_failure`) — если библиотеки нет в этом процессе, просто не
# распознаём её ошибки как постоянные (безопасный фолбэк — тогда работает
# прежнее поведение "повторять бесконечно", не хуже, чем было).
try:
    from aiogram.exceptions import TelegramForbiddenError, TelegramNotFound
except ImportError:  # процесс channel-vk
    TelegramForbiddenError = None  # type: ignore[assignment, misc]
    TelegramNotFound = None  # type: ignore[assignment, misc]

try:
    from vkbottle import VKAPIError
except ImportError:  # процесс channel-telegram
    VKAPIError = None  # type: ignore[assignment, misc]

logger = structlog.get_logger(__name__)

_PROCESS_BATCH_LIMIT = 50

# VK: 900 — получатель в чёрном списке сообщества, 901 — нельзя написать
# первым (получатель не разрешил сообщения), 902 — получатель явно запретил
# сообщения от сообщества. Все три не исправляются повтором — только самим
# получателем (или фоновой сверкой `ChannelBinding.messages_allowed`, см.
# DECISIONS_LOG.md №61 — но это отдельный, more proactive механизм, не
# спасает от гонки "запретил прямо между сверкой и отправкой").
_VK_PERMANENT_ERROR_CODES = frozenset({900, 901, 902})


def _is_permanent_failure(exc: Exception) -> bool:
    """Провайдер ответит ИДЕНТИЧНО на любую повторную попытку — получатель
    заблокировал бота/аккаунт удалён (Telegram) или запретил сообщения (VK).
    Найдено на первой боевой рассылке (DECISIONS_LOG.md): без этой проверки
    такие получатели уходили бы в бесконечный цикл повторов фоновым циклом
    раз в тик — навсегда, т.к. блокировка сама собой не снимается."""
    if TelegramForbiddenError is not None and isinstance(
        exc, TelegramForbiddenError | TelegramNotFound
    ):
        return True
    if VKAPIError is not None and isinstance(exc, VKAPIError):
        return getattr(exc, "code", None) in _VK_PERMANENT_ERROR_CODES
    return False


class DeliveryOutcome(str, enum.Enum):
    """Возвращается из `send_or_enqueue` — три исхода немедленной попытки, а
    не просто bool ("получилось"/"нет"), т.к. UNDELIVERABLE требует другого
    UX, чем QUEUED (первое — "обратитесь в поддержку", второе — "мы попробуем
    ещё")."""

    DELIVERED = "delivered"
    QUEUED = "queued"
    UNDELIVERABLE = "undeliverable"


class DeliverableChannel(Protocol):
    """Структурный протокол вместо конкретного класса канала — `TelegramChannel`
    и `VkChannel` не имеют общего базового класса для этих методов (не входят
    в `BaseMessengerChannel`, см. `app/channels/base.py`), но обе реализации
    совпадают по сигнатуре — этого достаточно. Переиспользуется
    `app/services/notification_service.py`, чтобы не дублировать протокол."""

    async def send_message(self, external_user_id: str, text: str, **kwargs: Any) -> None: ...

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None: ...

    async def send_qr_code(
        self, external_user_id: str, qr_code_payload: str, *, caption: str | None = None
    ) -> None: ...


async def _dispatch(
    channel: DeliverableChannel,
    *,
    kind: PendingDeliveryKind,
    payload: dict[str, Any],
    external_user_id: str,
) -> None:
    """Единственное место, транслирующее (`kind`, `payload`) в вызов конкретного
    метода канала — используется и для немедленной попытки, и для докрутки из
    очереди, чтобы обе попытки гарантированно били одним и тем же способом."""
    if kind == PendingDeliveryKind.TEXT_MESSAGE:
        await channel.send_message(external_user_id, payload["text"])
    elif kind == PendingDeliveryKind.QR_CODE:
        await channel.send_qr_code(
            external_user_id, payload["qr_code_payload"], caption=payload.get("caption")
        )
    elif kind == PendingDeliveryKind.DELIVER_PURCHASE:
        await channel.deliver_purchase(
            external_user_id,
            poster_path=payload.get("poster_path"),
            codes=payload["codes"],
            intro=payload["intro"],
        )


async def send_or_enqueue(
    db: Database,
    *,
    channel: DeliverableChannel | None,
    channel_type: ChannelType,
    external_user_id: str,
    kind: PendingDeliveryKind,
    payload: dict[str, Any],
    participant_id: int | None = None,
) -> DeliveryOutcome:
    """Пытается доставить прямо сейчас (с ретраями/backoff, прерываются
    досрочно для заведомо постоянных сбоев — см. `_is_permanent_failure`).
    `DELIVERED` — ушло в рамках этого вызова. `QUEUED` — не ушло, но НЕ
    потеряно: поставлено в очередь на гарантированную докрутку фоновым циклом.
    `UNDELIVERABLE` — доставить в принципе невозможно (получатель заблокировал
    канал/аккаунт удалён) — строка тоже пишется в `pending_channel_deliveries`
    (для истории/панели), но сразу терминальным статусом, БЕЗ докрутки: фоновый
    цикл получил бы тот же самый отказ бесконечно, впустую. `channel=None`
    (токен канала не задан в ЭТОМ процессе) — попытка вообще не делается, сразу
    `QUEUED`: докрутится, когда канал появится (в этом же процессе после
    переконфигурации/деплоя, либо в другом — `process_pending_deliveries` сам
    резолвит канал по `channel_type` на каждом тике)."""
    error: Exception | None = None
    permanent = False
    if channel is not None:
        try:
            await send_with_retry(
                lambda: _dispatch(
                    channel, kind=kind, payload=payload, external_user_id=external_user_id
                ),
                is_permanent=_is_permanent_failure,
            )
            return DeliveryOutcome.DELIVERED
        except Exception as exc:
            error = exc
            permanent = _is_permanent_failure(exc)

    with db.session() as session:
        session.add(
            PendingChannelDelivery(
                channel=channel_type,
                external_user_id=external_user_id,
                kind=kind,
                payload=payload,
                participant_id=participant_id,
                attempts=0,
                status=(
                    PendingDeliveryStatus.UNDELIVERABLE
                    if permanent
                    else PendingDeliveryStatus.PENDING
                ),
                last_error=str(error) if permanent and error is not None else None,
            )
        )
    logger.warning(
        (
            "channel_delivery_undeliverable"
            if permanent
            else "channel_delivery_enqueued_after_immediate_failure"
        ),
        channel=channel_type.value,
        kind=kind.value,
        participant_id=participant_id,
        error=str(error) if error is not None else "channel not configured in this process",
    )
    return DeliveryOutcome.UNDELIVERABLE if permanent else DeliveryOutcome.QUEUED


def _resolve_channel(
    channel_type: ChannelType,
    *,
    telegram_channel: DeliverableChannel | None,
    vk_channel: DeliverableChannel | None,
) -> DeliverableChannel | None:
    if channel_type == ChannelType.TELEGRAM:
        return telegram_channel
    if channel_type == ChannelType.VK:
        return vk_channel
    return None


async def process_pending_deliveries(
    db: Database,
    *,
    telegram_channel: DeliverableChannel | None,
    vk_channel: DeliverableChannel | None,
    batch_limit: int = _PROCESS_BATCH_LIMIT,
) -> None:
    """Один тик докрутки очереди — вызывается из фонового цикла backend
    (`backend/background/__init__.py`) с тем же периодом, что и остальная
    сверка. Одна попытка на строку за тик (без внутреннего backoff — интервал
    между тиками сам по себе служит паузой между повторами); при ВРЕМЕННОЙ
    неудаче строка остаётся PENDING и будет подхвачена следующим тиком — так
    до бесконечности, доставка не считается потерянной ни при каком числе
    неудач. При ПОСТОЯННОЙ неудаче (`_is_permanent_failure` — получатель
    заблокировал канал/аккаунт удалён) строка сразу переводится в терминальный
    `UNDELIVERABLE` и больше не трогается — иначе фоновый цикл бесконечно бил
    бы по заведомо обречённому получателю на каждом тике, впустую."""
    with db.session() as session:
        ids = list(
            session.execute(
                select(PendingChannelDelivery.id)
                .where(PendingChannelDelivery.status == PendingDeliveryStatus.PENDING)
                .order_by(PendingChannelDelivery.created_at)
                .limit(batch_limit)
            )
            .scalars()
            .all()
        )

    for delivery_id in ids:
        with db.session() as session:
            row = session.get(PendingChannelDelivery, delivery_id)
            if row is None or row.status != PendingDeliveryStatus.PENDING:
                continue  # гонка с другим тиком/ручной обработкой — пропускаем
            channel = _resolve_channel(
                row.channel, telegram_channel=telegram_channel, vk_channel=vk_channel
            )
            if channel is None:
                continue  # этот процесс не поднял нужный канал — ждём следующий тик
            row.attempts += 1
            row.last_attempt_at = utcnow()
            try:
                await _dispatch(
                    channel,
                    kind=row.kind,
                    payload=row.payload,
                    external_user_id=row.external_user_id,
                )
            except Exception as exc:
                row.last_error = str(exc)
                if _is_permanent_failure(exc):
                    row.status = PendingDeliveryStatus.UNDELIVERABLE
                    logger.warning(
                        "channel_delivery_became_undeliverable",
                        delivery_id=delivery_id,
                        channel=row.channel.value,
                        attempts=row.attempts,
                        error=str(exc),
                    )
                else:
                    logger.warning(
                        "channel_delivery_retry_failed",
                        delivery_id=delivery_id,
                        channel=row.channel.value,
                        attempts=row.attempts,
                        error=str(exc),
                    )
            else:
                row.status = PendingDeliveryStatus.SENT
                row.sent_at = utcnow()
                logger.info(
                    "channel_delivery_retry_succeeded",
                    delivery_id=delivery_id,
                    channel=row.channel.value,
                    attempts=row.attempts,
                )
