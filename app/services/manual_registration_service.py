"""Сервис ручных (офлайн) регистраций (п.7.5, 7.7, 8.2 ТЗ). Единственная точка
входа для бизнес-логики офлайн-продаж — API панели/бот-оператор вызывают только
эти функции.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select, update

from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import ManualRegistrationStatus, TicketSource
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.ticket import Ticket
from app.repositories import ticket_pool_repo as repo
from app.services import participant_service


class GiveawayNotSellableError(Exception):
    pass


class ManualRegistrationStateError(Exception):
    """Недопустимый переход статуса: повторное подтверждение, отмена после выдачи
    и т.п. (п.7.7 ТЗ)."""


class _InsufficientTickets(Exception):
    def __init__(self, free_count: int) -> None:
        super().__init__(f"Недостаточно свободных номеров: {free_count}")
        self.free_count = free_count


class _PendingLimitExceeded(Exception):
    def __init__(self, *, pending_quantity: int, limit: int) -> None:
        super().__init__(
            f"Превышен лимит ожидающих экземпляров: {pending_quantity} + новая покупка > {limit}"
        )
        self.pending_quantity = pending_quantity
        self.limit = limit


class _ParticipantBlocked(Exception):
    def __init__(self) -> None:
        super().__init__("Участник заблокирован")


@dataclass(frozen=True)
class CreateManualRegistrationOutcome:
    ok: bool
    manual_registration_id: int | None
    free_count: int
    pending_limit_exceeded: bool = False
    """True — отказ из-за превышения лимита суммарного количества экземпляров во
    всех текущих незавершённых покупках участника (продуктовое правило, см.
    DECISIONS.md №45), а не нехватки номеров."""
    pending_quantity: int = 0
    """Сколько экземпляров уже "висит" в незавершённых покупках участника —
    заполняется только при pending_limit_exceeded=True."""
    pending_limit: int = 0
    """Настроенный лимит — заполняется только при pending_limit_exceeded=True."""
    participant_blocked: bool = False
    """True — отказ из-за Participant.is_blocked (участник заблокирован в панели)."""


def create_manual_registration(
    db: Database,
    *,
    giveaway_id: int,
    participant_id: int,
    quantity: int,
    operator_id: int,
    ttl_seconds: int,
    comment: str | None = None,
) -> CreateManualRegistrationOutcome:
    """Создаёт ручную регистрацию (`PENDING`) и атомарно резервирует номера
    (п.7.5, 7.7, 8.2 ТЗ). Если свободных номеров недостаточно — регистрация
    не создаётся вовсе (всё откатывается), возвращается актуальный остаток.
    """
    with db.immediate_session() as session:
        giveaway = session.execute(select(Giveaway).where(Giveaway.id == giveaway_id)).scalar_one()
        if giveaway.opened_at is None or not giveaway.is_registration_open:
            raise GiveawayNotSellableError("Регистрация на розыгрыш не открыта")
        if giveaway.is_locked:
            raise GiveawayNotSellableError("Розыгрыш заблокирован (is_locked)")
        if participant_service.is_participant_blocked(session, participant_id=participant_id):
            raise _ParticipantBlocked()
        pending = participant_service.pending_ticket_quantity(
            session, participant_id=participant_id
        )
        limit = db.settings.max_pending_tickets_per_participant
        if pending + quantity > limit:
            raise _PendingLimitExceeded(pending_quantity=pending, limit=limit)

        registration = ManualRegistration(
            participant_id=participant_id,
            giveaway_id=giveaway_id,
            quantity=quantity,
            status=ManualRegistrationStatus.PENDING,
            operator_id=operator_id,
            comment=comment,
        )
        session.add(registration)
        session.flush()

        result = repo.reserve_tickets(
            session,
            giveaway_id=giveaway_id,
            quantity=quantity,
            participant_id=participant_id,
            manual_registration_id=registration.id,
            reserved_until=utcnow() + dt.timedelta(seconds=ttl_seconds),
        )
        if not result.ok:
            raise _InsufficientTickets(result.free_count_at_attempt)

        return CreateManualRegistrationOutcome(
            ok=True, manual_registration_id=registration.id, free_count=0
        )


def create_manual_registration_safe(
    db: Database,
    *,
    giveaway_id: int,
    participant_id: int,
    quantity: int,
    operator_id: int,
    ttl_seconds: int,
    comment: str | None = None,
) -> CreateManualRegistrationOutcome:
    try:
        return create_manual_registration(
            db,
            giveaway_id=giveaway_id,
            participant_id=participant_id,
            quantity=quantity,
            operator_id=operator_id,
            ttl_seconds=ttl_seconds,
            comment=comment,
        )
    except _InsufficientTickets as exc:
        return CreateManualRegistrationOutcome(
            ok=False, manual_registration_id=None, free_count=exc.free_count
        )
    except _PendingLimitExceeded as exc:
        return CreateManualRegistrationOutcome(
            ok=False,
            manual_registration_id=None,
            free_count=0,
            pending_limit_exceeded=True,
            pending_quantity=exc.pending_quantity,
            pending_limit=exc.limit,
        )
    except _ParticipantBlocked:
        return CreateManualRegistrationOutcome(
            ok=False, manual_registration_id=None, free_count=0, participant_blocked=True
        )


@dataclass(frozen=True)
class ConfirmOutcome:
    manual_registration_id: int
    tickets: list[Ticket]


def confirm_manual_registration(
    db: Database, *, manual_registration_id: int, now: dt.datetime | None = None
) -> ConfirmOutcome:
    """Подтверждение (`PENDING -> CONFIRMED`): выдаёт зарезервированные номера
    (п.7.7 ТЗ). Повторное подтверждение уже подтверждённой регистрации запрещено —
    выбрасывает `ManualRegistrationStateError` (а не тихий no-op)."""
    now = now or utcnow()
    with db.immediate_session() as session:
        result = session.execute(
            update(ManualRegistration)
            .where(
                ManualRegistration.id == manual_registration_id,
                ManualRegistration.status == ManualRegistrationStatus.PENDING,
            )
            .values(status=ManualRegistrationStatus.CONFIRMED, confirmed_at=now)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            registration = session.get(ManualRegistration, manual_registration_id)
            if registration is None:
                raise ManualRegistrationStateError(
                    f"Регистрация {manual_registration_id} не найдена"
                )
            raise ManualRegistrationStateError(
                f"Регистрация {manual_registration_id} уже в статусе {registration.status}, "
                "повторное подтверждение запрещено"
            )

        registration = session.execute(
            select(ManualRegistration).where(ManualRegistration.id == manual_registration_id)
        ).scalar_one()
        giveaway = session.execute(
            select(Giveaway).where(Giveaway.id == registration.giveaway_id)
        ).scalar_one()

        issued_rows = repo.issue_reserved(
            session, manual_registration_id=manual_registration_id, issued_at=now
        )
        tickets: list[Ticket] = []
        for row in issued_rows:
            ticket = Ticket(
                giveaway_id=giveaway.id,
                pool_id=row.id,
                number=row.number,
                full_code=giveaway.format_code(row.number),
                participant_id=registration.participant_id,
                source=TicketSource.MANUAL,
                manual_registration_id=registration.id,
            )
            session.add(ticket)
            tickets.append(ticket)
        session.flush()

        return ConfirmOutcome(manual_registration_id=manual_registration_id, tickets=tickets)


def cancel_manual_registration(
    db: Database, *, manual_registration_id: int, now: dt.datetime | None = None
) -> None:
    """Отмена регистрации — возможна ТОЛЬКО до выдачи номерков (статус `PENDING`,
    п.7.7 ТЗ). Отмена уже подтверждённой/отменённой регистрации запрещена."""
    now = now or utcnow()
    with db.immediate_session() as session:
        result = session.execute(
            update(ManualRegistration)
            .where(
                ManualRegistration.id == manual_registration_id,
                ManualRegistration.status == ManualRegistrationStatus.PENDING,
            )
            .values(status=ManualRegistrationStatus.CANCELLED, cancelled_at=now)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            registration = session.get(ManualRegistration, manual_registration_id)
            if registration is None:
                raise ManualRegistrationStateError(
                    f"Регистрация {manual_registration_id} не найдена"
                )
            raise ManualRegistrationStateError(
                f"Регистрация {manual_registration_id} в статусе {registration.status} — "
                "отмена возможна только до подтверждения (PENDING)"
            )
        repo.release_reservation(session, manual_registration_id=manual_registration_id)
