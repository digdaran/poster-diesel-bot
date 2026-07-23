"""PaymentReceipt — квитанция об оплате, присланная участником (не распознаётся,
только хранится для просмотра оператором в панели, см. ARCHITECTURE.md)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column

if TYPE_CHECKING:
    from app.models.payment import Payment


class PaymentReceipt(Base):
    __tablename__ = "payment_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Идентификаторы вложения у мессенджера, откуда пришла квитанция — не
    # используются для повторного скачивания (файл уже сохранён на диск), только
    # для отладки/трассировки.
    telegram_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vk_attachment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[dt.datetime] = created_at_column(index=True)

    payment: Mapped[Payment] = relationship(back_populates="receipts")
