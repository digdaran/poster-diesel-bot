"""Атомарные операции с пулом номеров (TicketPool) — п.7.5 ТЗ.

Все функции этого модуля ожидают, что вызывающая сторона уже открыла транзакцию
`BEGIN IMMEDIATE` (см. `app.core.db.Database.immediate_session`). Это единственное
место в системе, где выполняется "сырой" SQL для критичных к согласованности операций
с пулом номеров.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.enums import TicketPoolStatus
from app.models.giveaway import Giveaway
from app.models.ticket_pool import TicketPool


def materialize_pool(session: Session, giveaway: Giveaway) -> None:
    """Единовременно создаёт все номера `1..max_tickets` со случайным shuffle_order.

    Вызывается ровно один раз при открытии регистрации розыгрыша (п.7.2, 7.5 ТЗ).
    """
    numbers = list(range(1, giveaway.max_tickets + 1))
    shuffle_orders = list(range(1, giveaway.max_tickets + 1))
    random.shuffle(shuffle_orders)

    rows = [
        TicketPool(
            giveaway_id=giveaway.id, number=n, shuffle_order=so, status=TicketPoolStatus.FREE
        )
        for n, so in zip(numbers, shuffle_orders, strict=True)
    ]
    session.bulk_save_objects(rows)
    session.flush()


def count_free(session: Session, giveaway_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(TicketPool)
        .where(TicketPool.giveaway_id == giveaway_id, TicketPool.status == TicketPoolStatus.FREE)
    )
    return session.execute(stmt).scalar_one()


@dataclass(frozen=True)
class ReservationResult:
    """Результат попытки атомарного захвата номеров ("всё-или-ничего", п.7.5 ТЗ)."""

    reserved: list[TicketPool]
    free_count_at_attempt: int

    @property
    def ok(self) -> bool:
        return len(self.reserved) > 0


def reserve_tickets(
    session: Session,
    *,
    giveaway_id: int,
    quantity: int,
    participant_id: int,
    reserved_until: dt.datetime,
    payment_id: int | None = None,
    manual_registration_id: int | None = None,
) -> ReservationResult:
    """Атомарно захватывает `quantity` свободных номеров розыгрыша.

    Политика "всё-или-ничего": если свободных номеров меньше `quantity`, резерв НЕ
    создаётся, возвращается фактический остаток (`free_count_at_attempt`). Вызывающая
    сторона обязана выполнять эту функцию внутри `BEGIN IMMEDIATE`-транзакции.

    Ровно одна из `payment_id` / `manual_registration_id` должна быть передана.
    """
    if (payment_id is None) == (manual_registration_id is None):
        raise ValueError("Нужно передать ровно одну из payment_id / manual_registration_id")
    if quantity < 1:
        raise ValueError("quantity должно быть >= 1")

    free_ids_stmt = (
        select(TicketPool.id)
        .where(TicketPool.giveaway_id == giveaway_id, TicketPool.status == TicketPoolStatus.FREE)
        .order_by(TicketPool.shuffle_order)
        .limit(quantity)
    )
    free_ids = list(session.execute(free_ids_stmt).scalars().all())

    if len(free_ids) < quantity:
        actual_free = count_free(session, giveaway_id)
        return ReservationResult(reserved=[], free_count_at_attempt=actual_free)

    session.execute(
        update(TicketPool)
        .where(TicketPool.id.in_(free_ids))
        .values(
            status=TicketPoolStatus.RESERVED,
            participant_id=participant_id,
            payment_id=payment_id,
            manual_registration_id=manual_registration_id,
            reserved_until=reserved_until,
        )
    )
    session.execute(
        update(Giveaway)
        .where(Giveaway.id == giveaway_id)
        .values(tickets_reserved=Giveaway.tickets_reserved + quantity)
    )
    session.flush()

    reserved_rows = list(
        session.execute(select(TicketPool).where(TicketPool.id.in_(free_ids))).scalars().all()
    )
    return ReservationResult(reserved=reserved_rows, free_count_at_attempt=len(reserved_rows))


def release_reservation(
    session: Session,
    *,
    payment_id: int | None = None,
    manual_registration_id: int | None = None,
) -> int:
    """Возвращает зарезервированные номера в `free` (reserved -> free, п.7.5 ТЗ).

    Возвращает количество освобождённых строк (0, если резерва уже не было —
    операция идемпотентна и безопасна для повторного вызова).
    """
    if (payment_id is None) == (manual_registration_id is None):
        raise ValueError("Нужно передать ровно одну из payment_id / manual_registration_id")

    filters = [TicketPool.status == TicketPoolStatus.RESERVED]
    if payment_id is not None:
        filters.append(TicketPool.payment_id == payment_id)
    else:
        filters.append(TicketPool.manual_registration_id == manual_registration_id)

    rows = list(session.execute(select(TicketPool).where(*filters)).scalars().all())
    if not rows:
        return 0

    giveaway_id = rows[0].giveaway_id
    ids = [r.id for r in rows]

    session.execute(
        update(TicketPool)
        .where(TicketPool.id.in_(ids))
        .values(
            status=TicketPoolStatus.FREE,
            participant_id=None,
            payment_id=None,
            manual_registration_id=None,
            reserved_until=None,
        )
    )
    session.execute(
        update(Giveaway)
        .where(Giveaway.id == giveaway_id)
        .values(tickets_reserved=Giveaway.tickets_reserved - len(ids))
    )
    session.flush()
    return len(ids)


def issue_reserved(
    session: Session,
    *,
    payment_id: int | None = None,
    manual_registration_id: int | None = None,
    issued_at: dt.datetime,
) -> list[TicketPool]:
    """Переводит зарезервированные под платёж/регистрацию номера в `issued`.

    Не создаёт строки `Ticket` — это ответственность вызывающего сервиса (нужен
    доступ к `Giveaway.format_code`). Возвращает обновлённые строки пула.
    """
    if (payment_id is None) == (manual_registration_id is None):
        raise ValueError("Нужно передать ровно одну из payment_id / manual_registration_id")

    filters = [TicketPool.status == TicketPoolStatus.RESERVED]
    if payment_id is not None:
        filters.append(TicketPool.payment_id == payment_id)
    else:
        filters.append(TicketPool.manual_registration_id == manual_registration_id)

    rows = list(session.execute(select(TicketPool).where(*filters)).scalars().all())
    if not rows:
        return []

    giveaway_id = rows[0].giveaway_id
    ids = [r.id for r in rows]

    session.execute(
        update(TicketPool)
        .where(TicketPool.id.in_(ids))
        .values(status=TicketPoolStatus.ISSUED, issued_at=issued_at)
    )
    session.execute(
        update(Giveaway)
        .where(Giveaway.id == giveaway_id)
        .values(
            tickets_issued=Giveaway.tickets_issued + len(ids),
            tickets_reserved=Giveaway.tickets_reserved - len(ids),
        )
    )
    session.flush()
    return list(session.execute(select(TicketPool).where(TicketPool.id.in_(ids))).scalars().all())


def find_expired_reservation_refs(session: Session, *, now: dt.datetime) -> list[tuple[str, int]]:
    """Находит уникальные ссылки (payment_id/manual_registration_id) на просроченные
    резервы (`reserved_until < now`) для фонового освобождения (п.7.5 ТЗ).

    Возвращает список кортежей `("payment", payment_id)` / `("manual", manual_registration_id)`.
    """
    stmt = (
        select(TicketPool.payment_id, TicketPool.manual_registration_id)
        .where(
            TicketPool.status == TicketPoolStatus.RESERVED,
            TicketPool.reserved_until.is_not(None),
            TicketPool.reserved_until < now,
        )
        .distinct()
    )
    refs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for payment_id, manual_registration_id in session.execute(stmt).all():
        if payment_id is not None:
            key = ("payment", payment_id)
        elif manual_registration_id is not None:
            key = ("manual", manual_registration_id)
        else:
            continue
        if key not in seen:
            seen.add(key)
            refs.append(key)
    return refs
