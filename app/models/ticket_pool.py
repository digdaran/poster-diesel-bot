"""TicketPool — материализованный пул номеров розыгрыша, источник истины по занятости
(п.6.2, 7.5 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import TicketPoolStatus

if TYPE_CHECKING:
    from app.models.giveaway import Giveaway
    from app.models.manual_registration import ManualRegistration
    from app.models.payment import Payment
    from app.models.ticket import Ticket


class TicketPool(Base):
    __tablename__ = "ticket_pool"
    __table_args__ = (UniqueConstraint("giveaway_id", "number", name="uq_giveaway_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(ForeignKey("giveaways.id"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    shuffle_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[TicketPoolStatus] = mapped_column(
        SAEnum(TicketPoolStatus, native_enum=False),
        default=TicketPoolStatus.FREE,
        nullable=False,
        index=True,
    )
    participant_id: Mapped[int | None] = mapped_column(ForeignKey("participants.id"), nullable=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    manual_registration_id: Mapped[int | None] = mapped_column(
        ForeignKey("manual_registrations.id"), nullable=True
    )
    reserved_until: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    issued_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    giveaway: Mapped[Giveaway] = relationship(back_populates="ticket_pool_rows")
    payment: Mapped[Payment | None] = relationship(back_populates="pool_rows")
    manual_registration: Mapped[ManualRegistration | None] = relationship(
        back_populates="pool_rows"
    )
    ticket: Mapped[Ticket | None] = relationship(back_populates="pool_row", uselist=False)
