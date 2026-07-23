"""Payment — онлайн-платёж (п.6.2, 7.6, 9 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column
from app.models.enums import ChannelType, PaymentProviderType, PaymentStatus

if TYPE_CHECKING:
    from app.models.giveaway import Giveaway
    from app.models.participant import Participant
    from app.models.payment_receipt import PaymentReceipt
    from app.models.ticket import Ticket
    from app.models.ticket_pool import TicketPool


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "giveaway_id", "payment_number", name="uq_payments_giveaway_id_payment_number"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id"), nullable=False, index=True
    )
    giveaway_id: Mapped[int] = mapped_column(ForeignKey("giveaways.id"), nullable=False, index=True)
    provider: Mapped[PaymentProviderType] = mapped_column(
        SAEnum(PaymentProviderType, native_enum=False), nullable=False
    )
    # Мессенджер-канал, из которого создан платёж (Telegram/VK) — NULL для
    # платежей, созданных до появления этого поля (задним числом не восстанавливаем,
    # см. DECISIONS.md). Не путать с `provider` (банк-эквайер).
    channel: Mapped[ChannelType | None] = mapped_column(
        SAEnum(ChannelType, native_enum=False), nullable=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, native_enum=False),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True,
    )
    raw_webhook_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column(index=True)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    # Ссылка на оплату и содержимое QR (СБП) — one-shot данные от провайдера в
    # момент создания платежа (см. CreatedPayment), сохраняются здесь, т.к.
    # некоторые провайдеры (Т-Банк) не дают способа получить их повторно позже
    # (кнопка «Показать QR» в боте открывается отдельным событием без доступа
    # к транзиентному результату create_payment).
    payment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    qr_code_payload: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Внутренний ID платежа У БАНКА (не путать с нашим order_id) — некоторые
    # провайдеры (Т-Банк GetState) требуют именно его для резервной проверки
    # статуса, order_id для этого не подходит (см. DECISIONS.md).
    external_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Резервная проверка/поллинг (доп. поле сверх п.6.2, нужно для фоновой сверки
    # check_status — см. DECISIONS.md).
    poll_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Номер счёта на оплату в рамках розыгрыша (см. Giveaway.next_payment_number/
    # format_invoice_number) — заполняется только у провайдеров без резервирования
    # номерков "на лету" (см. requisites_qr); у старых платежей (Т-Банк/ВТБ/mock)
    # остаётся NULL — SQLite допускает несколько NULL в уникальном индексе.
    payment_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Деньги подтверждены (SUCCEEDED), но к этому моменту свободных номерков не
    # хватило на quantity — возврат вне объёма ТЗ §21, участнику нужно обращаться
    # в поддержку вручную. Только для видимости в админ-панели (см. DECISIONS.md).
    oversold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    participant: Mapped[Participant] = relationship(back_populates="payments")
    giveaway: Mapped[Giveaway] = relationship(back_populates="payments")
    pool_rows: Mapped[list[TicketPool]] = relationship(back_populates="payment")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="payment")
    receipts: Mapped[list[PaymentReceipt]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )
