"""Participant — участник розыгрыша (п.6.2 ТЗ).

Главный идентификатор — номер телефона (нормализованный, `app.core.phone`).
Привязки к мессенджерам вынесены в ChannelBinding; здесь нет полей конкретного канала.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column

if TYPE_CHECKING:
    from app.models.channel_binding import ChannelBinding
    from app.models.manual_registration import ManualRegistration
    from app.models.payment import Payment
    from app.models.ticket import Ticket


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column(index=True)

    channel_bindings: Mapped[list[ChannelBinding]] = relationship(
        back_populates="participant", cascade="all, delete-orphan"
    )
    payments: Mapped[list[Payment]] = relationship(back_populates="participant")
    manual_registrations: Mapped[list[ManualRegistration]] = relationship(
        back_populates="participant"
    )
    tickets: Mapped[list[Ticket]] = relationship(back_populates="participant")

    def recompute_phone_verified(self) -> None:
        """Пересчитывает `phone_verified` из привязок (производное поле, п.6.2 ТЗ)."""
        self.phone_verified = any(b.phone_verified for b in self.channel_bindings)

    @property
    def channels(self) -> list[str]:
        """Все мессенджер-каналы, к которым привязан участник (может быть больше
        одного — см. ChannelBinding) — для отображения в панели/отчётах."""
        return [b.channel.value for b in self.channel_bindings]
