"""Сервис пула номеров: материализация, резервирование, выдача, освобождение
(п.7.2, 7.5, 7.8 ТЗ). Единственная точка входа для бизнес-операций с TicketPool —
репозиторий (`app.repositories.ticket_pool_repo`) не вызывается напрямую извне.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import Database
from app.models.giveaway import Giveaway
from app.models.ticket_pool import TicketPool
from app.repositories import ticket_pool_repo as repo


class GiveawayNotSellableError(Exception):
    """Розыгрыш закрыт для продажи: is_registration_open=false, is_locked=true,
    либо не найден (п.7.8 ТЗ)."""


@dataclass(frozen=True)
class ReservationOutcome:
    ok: bool
    reserved: list[TicketPool]
    free_count: int
    """Актуальный остаток — заполняется всегда, даже при успехе (для UI)."""


def open_registration(
    session: Session, giveaway: Giveaway, *, now: dt.datetime | None = None
) -> None:
    """Открывает регистрацию розыгрыша и материализует пул номеров (п.7.2, 7.5 ТЗ).

    После вызова `prefix`/`ticket_price`/`max_tickets` считаются зафиксированными
    (проверка неизменяемости — на уровне API/сервиса розыгрышей, не здесь).
    """
    if giveaway.opened_at is not None:
        raise ValueError("Регистрация уже открыта — повторное открытие запрещено")
    if giveaway.max_tickets < 1 or giveaway.max_tickets > 100_000:
        raise ValueError("max_tickets должен быть в диапазоне 1..100000 (п.6.2 ТЗ)")

    giveaway.opened_at = now or dt.datetime.now(dt.timezone.utc)
    giveaway.is_registration_open = True
    session.add(giveaway)
    session.flush()
    repo.materialize_pool(session, giveaway)


def _check_sellable(giveaway: Giveaway) -> None:
    if giveaway.opened_at is None or not giveaway.is_registration_open:
        raise GiveawayNotSellableError("Регистрация на розыгрыш не открыта")
    if giveaway.is_locked:
        raise GiveawayNotSellableError("Розыгрыш заблокирован (is_locked)")


def reserve_for_payment(
    db: Database,
    *,
    giveaway_id: int,
    quantity: int,
    participant_id: int,
    payment_id: int,
    ttl_seconds: int,
    now: dt.datetime | None = None,
) -> ReservationOutcome:
    """Резервирует номера под онлайн-платёж. Выполняется в `BEGIN IMMEDIATE`-транзакции."""
    now = now or dt.datetime.now(dt.timezone.utc)
    with db.immediate_session() as session:
        giveaway = session.execute(select(Giveaway).where(Giveaway.id == giveaway_id)).scalar_one()
        _check_sellable(giveaway)
        result = repo.reserve_tickets(
            session,
            giveaway_id=giveaway_id,
            quantity=quantity,
            participant_id=participant_id,
            payment_id=payment_id,
            reserved_until=now + dt.timedelta(seconds=ttl_seconds),
        )
        free_count = (
            result.free_count_at_attempt if not result.ok else repo.count_free(session, giveaway_id)
        )
        return ReservationOutcome(ok=result.ok, reserved=result.reserved, free_count=free_count)


def reserve_for_manual_registration(
    db: Database,
    *,
    giveaway_id: int,
    quantity: int,
    participant_id: int,
    manual_registration_id: int,
    ttl_seconds: int,
    now: dt.datetime | None = None,
) -> ReservationOutcome:
    """Резервирует номера под ручную (офлайн) регистрацию (п.7.5, 7.7 ТЗ)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    with db.immediate_session() as session:
        giveaway = session.execute(select(Giveaway).where(Giveaway.id == giveaway_id)).scalar_one()
        _check_sellable(giveaway)
        result = repo.reserve_tickets(
            session,
            giveaway_id=giveaway_id,
            quantity=quantity,
            participant_id=participant_id,
            manual_registration_id=manual_registration_id,
            reserved_until=now + dt.timedelta(seconds=ttl_seconds),
        )
        free_count = (
            result.free_count_at_attempt if not result.ok else repo.count_free(session, giveaway_id)
        )
        return ReservationOutcome(ok=result.ok, reserved=result.reserved, free_count=free_count)


def release_payment_reservation(db: Database, *, payment_id: int) -> int:
    with db.immediate_session() as session:
        return repo.release_reservation(session, payment_id=payment_id)


def release_manual_registration_reservation(db: Database, *, manual_registration_id: int) -> int:
    with db.immediate_session() as session:
        return repo.release_reservation(session, manual_registration_id=manual_registration_id)


def get_free_count(db: Database, *, giveaway_id: int) -> int:
    with db.session() as session:
        return repo.count_free(session, giveaway_id)
