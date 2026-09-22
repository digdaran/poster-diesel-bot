"""Очередь гарантированной доставки сообщений через мессенджер-каналы
(DECISIONS_LOG.md №73) — по прямому запросу заказчика: доставка должна быть
гарантированной для конкретно взятого канала связи (без кросс-процессной
координации, см. `app/channels/retry.py`/№72).

`send_or_enqueue` — единая точка входа для всех проактивных/полу-проактивных
отправок (QR при создании счёта — `channels/*/handlers.py`; проактивные
уведомления об исходе платежа — `app/services/notification_service.py`):
сперва пытается отправить прямо сейчас (`send_with_retry`, до 3 попыток с
backoff в течение нескольких секунд), а если и это не удалось — НЕ считает
доставку потерянной, а кладёт `PendingChannelDelivery` со status=PENDING и
возвращает управление вызывающему немедленно (без исключения). Дальше
доставку докручивает фоновый цикл backend (`process_pending_deliveries`,
вызывается из `backend/background/__init__.py`) на каждом тике, пока не
пройдёт — без ограничения числа попыток."""

from __future__ import annotations

from typing import Any, Protocol

import structlog
from sqlalchemy import select

from app.channels.retry import send_with_retry
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import ChannelType, PendingDeliveryKind, PendingDeliveryStatus
from app.models.pending_channel_delivery import PendingChannelDelivery

logger = structlog.get_logger(__name__)

_PROCESS_BATCH_LIMIT = 50


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
    channel: DeliverableChannel,
    channel_type: ChannelType,
    external_user_id: str,
    kind: PendingDeliveryKind,
    payload: dict[str, Any],
    participant_id: int | None = None,
) -> bool:
    """Пытается доставить прямо сейчас (с ретраями/backoff); при исчерпании —
    ставит в очередь на гарантированную докрутку фоновым циклом и возвращает
    `False`, не пробрасывая исключение (вызывающий код не обязан знать про
    очередь — просто получает признак "ушло сейчас" или "ушло в фон"). `True` —
    доставлено в рамках этого вызова."""
    try:
        await send_with_retry(
            lambda: _dispatch(
                channel, kind=kind, payload=payload, external_user_id=external_user_id
            )
        )
        return True
    except Exception as exc:
        with db.session() as session:
            session.add(
                PendingChannelDelivery(
                    channel=channel_type,
                    external_user_id=external_user_id,
                    kind=kind,
                    payload=payload,
                    participant_id=participant_id,
                    attempts=0,
                )
            )
        logger.warning(
            "channel_delivery_enqueued_after_immediate_failure",
            channel=channel_type.value,
            kind=kind.value,
            participant_id=participant_id,
            error=str(exc),
        )
        return False


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
    между тиками сам по себе служит паузой между повторами); при неудаче
    строка остаётся PENDING и будет подхвачена следующим тиком — так до
    бесконечности, доставка не считается потерянной ни при каком числе неудач."""
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
