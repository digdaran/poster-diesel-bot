"""PanelUser — пользователь веб-панели (п.6.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column
from app.models.enums import PanelUserRole

if TYPE_CHECKING:
    from app.models.manual_registration import ManualRegistration


class PanelUser(Base):
    __tablename__ = "panel_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[PanelUserRole] = mapped_column(
        SAEnum(PanelUserRole, native_enum=False), nullable=False
    )
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    manual_registrations: Mapped[list[ManualRegistration]] = relationship(back_populates="operator")
