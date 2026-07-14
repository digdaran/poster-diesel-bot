"""ManualRegistration — ручная (офлайн) регистрация (п.6.2, 7.7 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column
from app.models.enums import ManualRegistrationStatus

if TYPE_CHECKING:
    from app.models.giveaway import Giveaway
    from app.models.panel_user import PanelUser
    from app.models.participant import Participant
    from app.models.ticket import Ticket
    from app.models.ticket_pool import TicketPool


class ManualRegistration(Base):
    __tablename__ = "manual_registrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id"), nullable=False, index=True
    )
    giveaway_id: Mapped[int] = mapped_column(ForeignKey("giveaways.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ManualRegistrationStatus] = mapped_column(
        SAEnum(ManualRegistrationStatus, native_enum=False),
        default=ManualRegistrationStatus.PENDING,
        nullable=False,
        index=True,
    )
    operator_id: Mapped[int] = mapped_column(
        ForeignKey("panel_users.id"), nullable=False, index=True
    )
    comment: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column(index=True)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    participant: Mapped[Participant] = relationship(back_populates="manual_registrations")
    giveaway: Mapped[Giveaway] = relationship(back_populates="manual_registrations")
    operator: Mapped[PanelUser] = relationship(back_populates="manual_registrations")
    pool_rows: Mapped[list[TicketPool]] = relationship(back_populates="manual_registration")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="manual_registration")
