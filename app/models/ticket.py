"""Ticket — выданный номерок (п.6.2 ТЗ). Создаётся в момент перехода строки пула в issued."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column
from app.models.enums import TicketSource

if TYPE_CHECKING:
    from app.models.giveaway import Giveaway
    from app.models.manual_registration import ManualRegistration
    from app.models.participant import Participant
    from app.models.payment import Payment
    from app.models.ticket_pool import TicketPool


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("giveaway_id", "number", name="uq_ticket_giveaway_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(ForeignKey("giveaways.id"), nullable=False, index=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("ticket_pool.id"), unique=True, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    full_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id"), nullable=False, index=True
    )
    source: Mapped[TicketSource] = mapped_column(
        SAEnum(TicketSource, native_enum=False), nullable=False
    )
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    manual_registration_id: Mapped[int | None] = mapped_column(
        ForeignKey("manual_registrations.id"), nullable=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()

    giveaway: Mapped[Giveaway] = relationship(back_populates="tickets")
    pool_row: Mapped[TicketPool] = relationship(back_populates="ticket")
    participant: Mapped[Participant] = relationship(back_populates="tickets")
    payment: Mapped[Payment | None] = relationship(back_populates="tickets")
    manual_registration: Mapped[ManualRegistration | None] = relationship(back_populates="tickets")
