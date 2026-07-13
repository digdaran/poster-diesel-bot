"""AuditLog — запись журнала аудита (п.6.2, 17 ТЗ). Append-only: только INSERT."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column
from app.models.enums import AuditActorType


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_type: Mapped[AuditActorType] = mapped_column(
        SAEnum(AuditActorType, native_enum=False), nullable=False
    )
    actor_id: Mapped[int | None] = mapped_column(nullable=True)
    actor_label: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()
