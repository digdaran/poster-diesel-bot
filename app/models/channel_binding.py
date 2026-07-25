"""ChannelBinding — привязка участника к каналу-мессенджеру (п.6.2 ТЗ).

Уникальные ограничения: пара (channel, external_user_id) уникальна; у участника
не более одной привязки на канал (составной unique по (participant_id, channel)).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow
from app.models.enums import ChannelType

if TYPE_CHECKING:
    from app.models.participant import Participant


class ChannelBinding(Base):
    __tablename__ = "channel_bindings"
    __table_args__ = (
        UniqueConstraint("channel", "external_user_id", name="uq_channel_external_user"),
        UniqueConstraint("participant_id", "channel", name="uq_participant_channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id"), nullable=False, index=True
    )
    channel: Mapped[ChannelType] = mapped_column(
        SAEnum(ChannelType, native_enum=False), nullable=False
    )
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    messages_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    """Разрешено ли каналу писать участнику первым (проактивно), там, где это
    отзываемо (VK: событие `message_allow`/`message_deny`, см. DECISIONS_LOG.md #32).
    `NULL` — канал не отслеживает это отдельно от `can_initiate_dialog` (напр.
    Telegram: право неявно и не отзывается пользователем)."""
    linked_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    participant: Mapped[Participant] = relationship(back_populates="channel_bindings")
