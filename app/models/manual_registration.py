"""ManualRegistration — ручная (офлайн) регистрация (п.6.2, 7.7 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column
from app.models.enums import ManualRegistrationPaymentMethod, ManualRegistrationStatus

if TYPE_CHECKING:
    from app.models.giveaway import Giveaway
    from app.models.panel_user import PanelUser
    from app.models.participant import Participant
    from app.models.ticket import Ticket
    from app.models.ticket_pool import TicketPool


class ManualRegistration(Base):
    __tablename__ = "manual_registrations"
    __table_args__ = (
        UniqueConstraint(
            "giveaway_id",
            "payment_number",
            name="uq_manual_registrations_giveaway_id_payment_number",
        ),
    )

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

    # Наличные оператору в кассу (по умолчанию, как раньше) или безналичный QR по
    # реквизитам, сформированный оператором по просьбе покупателя — см.
    # DECISIONS.md. Влияет только на разметку выручки в отчётах (кассовая vs
    # безналичная) и на то, что `payment_number`/`qr_code_payload` ниже заполнены;
    # сама выдача номерков через `confirm_manual_registration` не меняется.
    payment_method: Mapped[ManualRegistrationPaymentMethod] = mapped_column(
        SAEnum(ManualRegistrationPaymentMethod, native_enum=False),
        default=ManualRegistrationPaymentMethod.CASH,
        nullable=False,
    )
    # Номер счёта на оплату — из ТОГО ЖЕ счётчика Giveaway.next_payment_number, что
    # и у онлайн-платежей (см. Payment.payment_number), поэтому PREFIX-NNNNN
    # остаётся уникальным в рамках розыгрыша независимо от источника. Заполняется
    # только когда оператор реально сформировал QR (CASHLESS), иначе NULL.
    payment_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Собранный ST00012-payload на момент генерации — хранится (а не пересобирается
    # на лету), чтобы GET .../qr.png не зависел от того, не поменялись ли реквизиты
    # в настройках после показа QR покупателю (тот же принцип, что и
    # Payment.qr_code_payload).
    qr_code_payload: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    qr_generated_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    participant: Mapped[Participant] = relationship(back_populates="manual_registrations")
    giveaway: Mapped[Giveaway] = relationship(back_populates="manual_registrations")
    operator: Mapped[PanelUser] = relationship(back_populates="manual_registrations")
    pool_rows: Mapped[list[TicketPool]] = relationship(back_populates="manual_registration")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="manual_registration")
