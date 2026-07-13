"""Broadcast — рассылка (п.6.2, 15 ТЗ). Только Telegram-привязки (продуктовое решение)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column
from app.models.enums import BroadcastStatus


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    audience_filter: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[BroadcastStatus] = mapped_column(
        SAEnum(BroadcastStatus, native_enum=False), default=BroadcastStatus.DRAFT, nullable=False
    )
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
    sent_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
