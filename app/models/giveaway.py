"""Giveaway — розыгрыш (п.6.2, 7.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column

if TYPE_CHECKING:
    from app.models.giveaway_poster import GiveawayPoster
    from app.models.manual_registration import ManualRegistration
    from app.models.payment import Payment
    from app.models.ticket import Ticket
    from app.models.ticket_pool import TicketPool

MAX_TICKETS_LIMIT = 100_000
TICKET_NUMBER_WIDTH = 6
INVOICE_NUMBER_WIDTH = 5


class Giveaway(Base):
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    ticket_price: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tickets: Mapped[int] = mapped_column(Integer, nullable=False)
    tickets_issued: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tickets_reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Счётчик номеров счетов на оплату (п. "Оплата по счету № {prefix}-{NNNNN}") —
    # уникален в рамках розыгрыша, инкрементируется в той же BEGIN IMMEDIATE
    # транзакции, что и создание Payment (тот же паттерн блокировки писателя, что
    # уже используется для tickets_reserved), см. app/services/payment_service.py.
    next_payment_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_registration_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Архивация (не удаление, см. DECISIONS_LOG.md) — скрывает коллекцию из
    # основного раздела «Коллекции» в отдельный «Архив», не трогая ни одной
    # связанной записи (Ticket/Payment/ManualRegistration/аудит остаются как есть).
    # Обратима (`unarchive`). Разрешена только когда is_registration_open=False
    # и нет ни одной PENDING заявки — см. backend/api/giveaways.py::archive_giveaway.
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    opened_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    digital_poster_caption: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    ticket_pool_rows: Mapped[list[TicketPool]] = relationship(back_populates="giveaway")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="giveaway")
    payments: Mapped[list[Payment]] = relationship(back_populates="giveaway")
    manual_registrations: Mapped[list[ManualRegistration]] = relationship(back_populates="giveaway")
    posters: Mapped[list[GiveawayPoster]] = relationship(
        back_populates="giveaway", cascade="all, delete-orphan", order_by="GiveawayPoster.id"
    )

    @property
    def free_tickets_count(self) -> int:
        return self.max_tickets - self.tickets_issued - self.tickets_reserved

    @property
    def is_open_for_sale(self) -> bool:
        """Продажа/выдача возможна (п.7.8 ТЗ, без учёта блокировки конкретного участника)."""
        return self.is_registration_open and not self.is_locked and self.free_tickets_count > 0

    def format_code(self, number: int) -> str:
        return f"{self.prefix}-{number:0{TICKET_NUMBER_WIDTH}d}"

    def format_invoice_number(self, number: int) -> str:
        """Номер счёта на оплату для назначения платежа, уникален в рамках
        розыгрыша (не путать с format_code — номерки и счета имеют раздельные
        счётчики и разную ширину)."""
        return f"{self.prefix}-{number:0{INVOICE_NUMBER_WIDTH}d}"
