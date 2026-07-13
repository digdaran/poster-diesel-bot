"""Giveaway — розыгрыш (п.6.2, 7.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column

if TYPE_CHECKING:
    from app.models.manual_registration import ManualRegistration
    from app.models.payment import Payment
    from app.models.ticket import Ticket
    from app.models.ticket_pool import TicketPool

MAX_TICKETS_LIMIT = 100_000
TICKET_NUMBER_WIDTH = 6


class Giveaway(Base):
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    ticket_price: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tickets: Mapped[int] = mapped_column(Integer, nullable=False)
    tickets_issued: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tickets_reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_registration_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    opened_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    digital_poster_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    digital_poster_caption: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    poster_media_cache: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    ticket_pool_rows: Mapped[list[TicketPool]] = relationship(back_populates="giveaway")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="giveaway")
    payments: Mapped[list[Payment]] = relationship(back_populates="giveaway")
    manual_registrations: Mapped[list[ManualRegistration]] = relationship(back_populates="giveaway")

    @property
    def free_tickets_count(self) -> int:
        return self.max_tickets - self.tickets_issued - self.tickets_reserved

    @property
    def is_open_for_sale(self) -> bool:
        """Продажа/выдача возможна (п.7.8 ТЗ, без учёта блокировки конкретного участника)."""
        return self.is_registration_open and not self.is_locked and self.free_tickets_count > 0

    def format_code(self, number: int) -> str:
        return f"{self.prefix}-{number:0{TICKET_NUMBER_WIDTH}d}"
