"""Общая постраничная выдача для списков панели (Продажи/Номерки/Ручные
регистрации/Участники).

Использование: сначала собрать "чистый" stmt с примененными `.where(...)`
фильтрами (без `joinedload`/`order_by`), посчитать `count_total`, затем
поверх ТОГО ЖЕ stmt добавить `joinedload`/`order_by`/`limit`/`offset` для
самой страницы — так COUNT-подзапрос не тащит лишние JOIN'ы и сортировку.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

PAGE_SIZES = (10, 50, 100, 500)


def validate_page_size(page_size: int) -> int:
    """`Literal[10, 50, 100, 500]` как тип query-параметра не коэрсит строковые
    значения ("10") в этой версии Pydantic/FastAPI — валидные значения тоже
    отклонялись. Проверяем вручную вместо этого."""
    if page_size not in PAGE_SIZES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"page_size должен быть одним из {PAGE_SIZES}",
        )
    return page_size


def count_total(session: Session, stmt: Select[Any]) -> int:
    return session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()


def page_bounds(*, page: int, page_size: int) -> tuple[int, int]:
    """`(limit, offset)` для `.limit(limit).offset(offset)`."""
    return page_size, (page - 1) * page_size
