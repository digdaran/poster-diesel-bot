"""PlatformSettings — глобальные настройки (п.6.2 ТЗ). Единственная строка (id=1, singleton)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import PaymentProviderType

SINGLETON_ID = 1


class PlatformSettings(Base):
    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=SINGLETON_ID)
    payment_provider_override: Mapped[PaymentProviderType | None] = mapped_column(
        SAEnum(PaymentProviderType, native_enum=False), nullable=True
    )
    ignore_phone_verification: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    online_status_poll_interval_sec: Mapped[int] = mapped_column(
        Integer, default=60, nullable=False
    )
    online_status_poll_max_attempts: Mapped[int] = mapped_column(
        Integer, default=10, nullable=False
    )
    online_reservation_ttl_sec: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
    manual_reservation_ttl_sec: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    support_contacts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("panel_users.id"), nullable=True)
