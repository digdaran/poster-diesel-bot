"""Базовый declarative-класс и общие типы колонок."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.orm import registry as sa_registry


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    registry = sa_registry()


def created_at_column():  # type: ignore[no-untyped-def]
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
