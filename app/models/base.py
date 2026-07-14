"""Базовый declarative-класс и общие типы колонок."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.orm import registry as sa_registry


def utcnow() -> dt.datetime:
    """UTC-время БЕЗ tzinfo (naive).

    SQLite не хранит timezone-информацию надёжно при round-trip через
    SQLAlchemy (значения возвращаются naive), поэтому вся система единообразно
    работает с naive-datetime, семантически всегда означающими UTC (п.6.1 ТЗ:
    \"все временные метки хранятся в UTC\"). Сравнивать datetime из разных
    источников следует только через эту функцию/её результаты.
    """
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    registry = sa_registry()


def created_at_column(*, index: bool = False):  # type: ignore[no-untyped-def]
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=index)
