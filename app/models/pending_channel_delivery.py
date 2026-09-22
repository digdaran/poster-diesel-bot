"""PendingChannelDelivery — очередь гарантированной доставки сообщений через
мессенджер-каналы (`app/services/channel_delivery_queue.py`, DECISIONS_LOG.md №73).

Строка создаётся, когда `send_with_retry` (немедленные попытки с backoff, см.
`app/channels/retry.py`) исчерпал попытки прямо в моменте отправки — доставка
НЕ считается потерянной, а докручивается фоновым циклом backend
(`backend/background/__init__.py`) до успеха, без ограничения числа попыток
(явное требование заказчика: гарантия по конкретному каналу, не кросс-процессная
координация)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column
from app.models.enums import ChannelType, PendingDeliveryKind, PendingDeliveryStatus


class PendingChannelDelivery(Base):
    __tablename__ = "pending_channel_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[ChannelType] = mapped_column(
        SAEnum(ChannelType, native_enum=False), nullable=False, index=True
    )
    external_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[PendingDeliveryKind] = mapped_column(
        SAEnum(PendingDeliveryKind, native_enum=False), nullable=False
    )
    # Аргументы вызова метода канала, специфичные для `kind` — см.
    # `channel_delivery_queue._dispatch` за точной формой для каждого kind.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[PendingDeliveryStatus] = mapped_column(
        SAEnum(PendingDeliveryStatus, native_enum=False),
        default=PendingDeliveryStatus.PENDING,
        nullable=False,
        index=True,
    )
    # Участник-получатель — только для контекста/отладки зависших доставок,
    # в логике очереди не используется (адресация целиком через channel+external_user_id).
    participant_id: Mapped[int | None] = mapped_column(
        ForeignKey("participants.id"), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column(index=True)
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
