"""GiveawayPoster — один из цифровых постеров розыгрыша (загружается через
веб-админку, см. DECISIONS.md №46). Розыгрышу можно привязать несколько
постеров; при выдаче участнику отправляется один случайно выбранный
(см. `_deliver_tickets` в `channels/telegram/handlers.py`/`channels/vk/handlers.py`)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column

if TYPE_CHECKING:
    from app.models.giveaway import Giveaway


class GiveawayPoster(Base):
    __tablename__ = "giveaway_posters"

    id: Mapped[int] = mapped_column(primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Необязательный кэш идентификаторов уже загруженного медиа по каналам
    # (см. ARCHITECTURE.md §7.1) — перенесено из прежнего Giveaway.poster_media_cache
    # без изменения назначения: заполняется каналом лениво, не источник истины.
    media_cache: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    giveaway: Mapped[Giveaway] = relationship(back_populates="posters")
