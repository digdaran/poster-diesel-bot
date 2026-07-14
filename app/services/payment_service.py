"""Сервис онлайн-платежей: создание с резервом номеров и идемпотентная финализация
(п.7.5, 7.6, 9 ТЗ). Единственная точка входа для всей платёжной бизнес-логики —
каналы и backend-роутеры вызывают только эти функции, никогда не работают
с моделью Payment/TicketPool напрямую.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update

from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import PaymentStatus, TicketSource
from app.models.giveaway import Giveaway
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.payments.base import BasePaymentProvider, CreatedPayment, PaymentOrder
from app.repositories import ticket_pool_repo as repo


class GiveawayNotSellableError(Exception):
    pass


@dataclass(frozen=True)
class CreatePaymentOutcome:
    ok: bool
    payment_id: int | None
    order_id: str | None
    created: CreatedPayment | None
    free_count: int
    """Актуальный остаток — заполняется при отказе (недостаточно номеров, п.7.5 ТЗ)."""
    amount: int | None = None
    """Сумма платежа в копейках (quantity * ticket_price) — заполняется при ok=True."""


def create_payment(
    db: Database,
    provider: BasePaymentProvider,
    *,
    giveaway_id: int,
    participant_id: int,
    participant_phone: str,
    quantity: int,
) -> CreatePaymentOutcome:
    """Создаёт платёж и резервирует номера атомарно (п.7.3, 7.5 ТЗ).

    Если свободных номеров недостаточно — платёж НЕ создаётся (ни в БД, ни в банке),
    возвращается актуальный остаток. Обращение к банку (create_payment на стороне
    провайдера, сетевой вызов) выполняется ПОСЛЕ успешного резерва и вне
    БД-транзакции — чтобы не удерживать write-lock SQLite на время сетевого I/O.
    """
    order_id = uuid.uuid4().hex

    with db.immediate_session() as session:
        giveaway = session.execute(select(Giveaway).where(Giveaway.id == giveaway_id)).scalar_one()
        if giveaway.opened_at is None or not giveaway.is_registration_open:
            raise GiveawayNotSellableError("Регистрация на розыгрыш не открыта")
        if giveaway.is_locked:
            raise GiveawayNotSellableError("Розыгрыш заблокирован (is_locked)")

        amount = quantity * giveaway.ticket_price
        payment = Payment(
            order_id=order_id,
            participant_id=participant_id,
            giveaway_id=giveaway_id,
            provider=provider.provider_type,
            amount=amount,
            quantity=quantity,
            status=PaymentStatus.PENDING,
        )
        session.add(payment)
        session.flush()  # получаем payment.id для ссылки в резерве

        result = repo.reserve_tickets(
            session,
            giveaway_id=giveaway_id,
            quantity=quantity,
            participant_id=participant_id,
            payment_id=payment.id,
            reserved_until=utcnow() + dt.timedelta(seconds=600),
        )
        if not result.ok:
            # Откатываем ВСЮ транзакцию — платёж не должен быть создан (п.7.5 ТЗ).
            raise _InsufficientTickets(result.free_count_at_attempt)

        payment_id = payment.id

    order = PaymentOrder(
        order_id=order_id,
        amount=amount,
        quantity=quantity,
        description=f"Розыгрыш #{giveaway_id}: {quantity} номерков",
        participant_phone=participant_phone,
    )
    created = provider.create_payment(order)

    with db.session() as session:
        session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(payment_url=created.payment_url, qr_code_payload=created.qr_code_payload)
        )

    return CreatePaymentOutcome(
        ok=True,
        payment_id=payment_id,
        order_id=order_id,
        created=created,
        free_count=0,
        amount=amount,
    )


class _InsufficientTickets(Exception):
    def __init__(self, free_count: int) -> None:
        super().__init__(f"Недостаточно свободных номеров: {free_count}")
        self.free_count = free_count


def create_payment_safe(
    db: Database,
    provider: BasePaymentProvider,
    *,
    giveaway_id: int,
    participant_id: int,
    participant_phone: str,
    quantity: int,
) -> CreatePaymentOutcome:
    """Обёртка над `create_payment`, превращающая нехватку номеров в обычный
    (не-исключительный) результат — удобно для вызова из ботов/API."""
    try:
        return create_payment(
            db,
            provider,
            giveaway_id=giveaway_id,
            participant_id=participant_id,
            participant_phone=participant_phone,
            quantity=quantity,
        )
    except _InsufficientTickets as exc:
        return CreatePaymentOutcome(
            ok=False, payment_id=None, order_id=None, created=None, free_count=exc.free_count
        )


@dataclass(frozen=True)
class FinalizeOutcome:
    applied: bool
    """False — платёж уже был финализирован ранее (no-op, повторный webhook/сверка)."""
    payment_id: int | None = None
    participant_id: int | None = None
    giveaway_id: int | None = None
    new_status: PaymentStatus | None = None
    tickets: list[Ticket] | None = None


def finalize_payment(
    db: Database,
    *,
    order_id: str,
    new_status: PaymentStatus,
    raw_payload: dict | None = None,
    now: dt.datetime | None = None,
) -> FinalizeOutcome:
    """Идемпотентная финализация платежа (п.7.6 ТЗ).

    Атомарный условный UPDATE `WHERE status='PENDING'` — если платёж уже
    финализирован (0 строк обновлено), это no-op: повторный webhook или гонка
    webhook/фоновой сверки не приводят к повторной выдаче номеров.
    """
    now = now or utcnow()
    if new_status not in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
        raise ValueError("new_status должен быть SUCCEEDED или FAILED")

    with db.immediate_session() as session:
        result = session.execute(
            update(Payment)
            .where(Payment.order_id == order_id, Payment.status == PaymentStatus.PENDING)
            .values(status=new_status, confirmed_at=now, raw_webhook_payload=raw_payload)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            return FinalizeOutcome(applied=False)

        payment = session.execute(select(Payment).where(Payment.order_id == order_id)).scalar_one()

        tickets: list[Ticket] = []
        if new_status == PaymentStatus.SUCCEEDED:
            giveaway = session.execute(
                select(Giveaway).where(Giveaway.id == payment.giveaway_id)
            ).scalar_one()
            issued_rows = repo.issue_reserved(session, payment_id=payment.id, issued_at=now)
            for row in issued_rows:
                ticket = Ticket(
                    giveaway_id=giveaway.id,
                    pool_id=row.id,
                    number=row.number,
                    full_code=giveaway.format_code(row.number),
                    participant_id=payment.participant_id,
                    source=TicketSource.ONLINE,
                    payment_id=payment.id,
                )
                session.add(ticket)
                tickets.append(ticket)
            session.flush()
        else:
            repo.release_reservation(session, payment_id=payment.id)

        return FinalizeOutcome(
            applied=True,
            payment_id=payment.id,
            participant_id=payment.participant_id,
            giveaway_id=payment.giveaway_id,
            new_status=new_status,
            tickets=tickets,
        )


@dataclass(frozen=True)
class PollResult:
    order_id: str
    outcome: FinalizeOutcome | None
    """None означает, что платёж всё ещё PENDING и не был финализирован в этом проходе."""


def poll_pending_payment(
    db: Database,
    provider: BasePaymentProvider,
    *,
    payment_id: int,
    max_attempts: int,
    ttl_seconds: int,
    now: dt.datetime | None = None,
) -> PollResult | None:
    """Резервная сверка одного PENDING-платежа (фоновая задача, п.7.5 ТЗ).

    Логика: банк вернул SUCCEEDED/FAILED -> финализация; "в процессе" -> инкремент
    poll_attempts и ожидание; лимит попыток или предельный TTL исчерпан -> платёж
    помечается FAILED, резерв освобождается.
    """
    now = now or utcnow()
    with db.session() as session:
        payment = session.get(Payment, payment_id)
        if payment is None or payment.status != PaymentStatus.PENDING:
            return None
        order_id = payment.order_id
        created_at = payment.created_at
        attempts = payment.poll_attempts

    bank_status = provider.check_status(order_id)

    if bank_status in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
        outcome = finalize_payment(db, order_id=order_id, new_status=bank_status, now=now)
        return PollResult(order_id=order_id, outcome=outcome)

    attempts += 1
    ttl_expired = (now - created_at).total_seconds() >= ttl_seconds
    attempts_exhausted = attempts >= max_attempts

    if ttl_expired or attempts_exhausted:
        outcome = finalize_payment(db, order_id=order_id, new_status=PaymentStatus.FAILED, now=now)
        return PollResult(order_id=order_id, outcome=outcome)

    with db.session() as session:
        session.execute(
            update(Payment).where(Payment.id == payment_id).values(poll_attempts=attempts)
        )
    return PollResult(order_id=order_id, outcome=None)
