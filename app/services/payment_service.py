"""Сервис онлайн-платежей: создание с резервом номеров и идемпотентная финализация
(п.7.5, 7.6, 9 ТЗ). Единственная точка входа для всей платёжной бизнес-логики —
каналы и backend-роутеры вызывают только эти функции, никогда не работают
с моделью Payment/TicketPool напрямую.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import AuditActorType, PaymentStatus, TicketSource
from app.models.giveaway import Giveaway
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.payments.base import BasePaymentProvider, CreatedPayment, PaymentOrder
from app.repositories import ticket_pool_repo as repo
from app.services import audit_service, participant_service

logger = structlog.get_logger(__name__)


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
    has_active_purchase: bool = False
    """True — отказ из-за уже существующей активной покупки участника (продуктовое
    правило, см. DECISIONS.md), а не нехватки номеров."""


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
        if participant_service.has_active_purchase(session, participant_id=participant_id):
            raise _ActivePurchaseExists()

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
        description=f"Постер «{giveaway.name}»: {quantity} шт.",
        participant_phone=participant_phone,
    )
    created = provider.create_payment(order)

    with db.session() as session:
        session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(
                payment_url=created.payment_url,
                qr_code_payload=created.qr_code_payload,
                external_payment_id=created.external_payment_id,
            )
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


class _ActivePurchaseExists(Exception):
    def __init__(self) -> None:
        super().__init__("У участника уже есть активная покупка")


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
    except _ActivePurchaseExists:
        return CreatePaymentOutcome(
            ok=False,
            payment_id=None,
            order_id=None,
            created=None,
            free_count=0,
            has_active_purchase=True,
        )


@dataclass(frozen=True)
class FinalizeOutcome:
    applied: bool
    """False — платёж уже был финализирован ранее (no-op, повторный webhook/сверка),
    либо (см. `late_success_no_tickets`) банк подтвердил оплату слишком поздно и
    номерки не удалось повторно захватить."""
    payment_id: int | None = None
    participant_id: int | None = None
    giveaway_id: int | None = None
    new_status: PaymentStatus | None = None
    tickets: list[Ticket] | None = None
    late_success_no_tickets: bool = False
    """True — банк подтвердил оплату уже ПОСЛЕ того, как платёж был помечен
    CANCELLED/FAILED (отмена участником или истечение TTL) и резерв был роздан
    другим, а повторно захватить `quantity` номерков не удалось (см. ТЗ, описание
    сценария "оплачено после освобождения" — кандидат на возврат). Авто-возврат
    не делается (ТЗ §21) — вызывающая сторона обязана уведомить участника
    отдельным сообщением (`notification_service.notify_late_success_no_tickets`)."""


def _recover_late_success(
    session: Session, *, payment: Payment, raw_payload: dict | None, now: dt.datetime
) -> FinalizeOutcome:
    """Банк подтвердил SUCCEEDED уже после того, как платёж был помечен
    CANCELLED/FAILED (и резерв роздан) — пытается заново захватить `quantity`
    номеров под тот же payment_id и выдать их, как будто платёж только что
    подтверждён. Намеренно НЕ проверяет `is_locked`/`is_registration_open`
    розыгрыша — деньги уже списаны, эти флаги гейтят только НОВЫЕ покупки."""
    result = repo.reserve_tickets(
        session,
        giveaway_id=payment.giveaway_id,
        quantity=payment.quantity,
        participant_id=payment.participant_id,
        payment_id=payment.id,
        reserved_until=now,
    )
    if not result.ok:
        audit_service.log(
            session,
            action="payment_late_success_no_tickets_available",
            actor_type=AuditActorType.SYSTEM,
            actor_label="finalize_payment",
            entity_type="payment",
            entity_id=payment.id,
            details={
                "order_id": payment.order_id,
                "participant_id": payment.participant_id,
                "giveaway_id": payment.giveaway_id,
                "quantity": payment.quantity,
                "amount": payment.amount,
                "previous_status": payment.status.value,
            },
        )
        return FinalizeOutcome(
            applied=False,
            payment_id=payment.id,
            participant_id=payment.participant_id,
            giveaway_id=payment.giveaway_id,
            late_success_no_tickets=True,
        )

    session.execute(
        update(Payment)
        .where(Payment.id == payment.id)
        .values(status=PaymentStatus.SUCCEEDED, confirmed_at=now, raw_webhook_payload=raw_payload)
    )
    giveaway = session.execute(
        select(Giveaway).where(Giveaway.id == payment.giveaway_id)
    ).scalar_one()
    issued_rows = repo.issue_reserved(session, payment_id=payment.id, issued_at=now)
    tickets = [
        Ticket(
            giveaway_id=giveaway.id,
            pool_id=row.id,
            number=row.number,
            full_code=giveaway.format_code(row.number),
            participant_id=payment.participant_id,
            source=TicketSource.ONLINE,
            payment_id=payment.id,
        )
        for row in issued_rows
    ]
    session.add_all(tickets)
    session.flush()
    audit_service.log(
        session,
        action="payment_late_success_recovered",
        actor_type=AuditActorType.SYSTEM,
        actor_label="finalize_payment",
        entity_type="payment",
        entity_id=payment.id,
        details={"order_id": payment.order_id, "quantity": payment.quantity},
    )
    return FinalizeOutcome(
        applied=True,
        payment_id=payment.id,
        participant_id=payment.participant_id,
        giveaway_id=payment.giveaway_id,
        new_status=PaymentStatus.SUCCEEDED,
        tickets=tickets,
    )


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
    финализирован (0 строк обновлено), это обычно no-op (повторный webhook или
    гонка webhook/фоновой сверки), КРОМЕ одного случая: банк сообщает SUCCEEDED,
    а у нас платёж уже CANCELLED/FAILED — тогда это "поздняя оплата после
    отказа/отмены", и вместо тихого no-op запускается восстановление
    (`_recover_late_success`), закрывающее пробел, ранее описанный в ТЗ, но
    никогда не реализованный.
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
            payment = session.execute(
                select(Payment).where(Payment.order_id == order_id)
            ).scalar_one_or_none()
            if (
                payment is not None
                and new_status == PaymentStatus.SUCCEEDED
                and payment.status in (PaymentStatus.CANCELLED, PaymentStatus.FAILED)
            ):
                return _recover_late_success(
                    session, payment=payment, raw_payload=raw_payload, now=now
                )
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
class CancelOutcome:
    applied: bool
    """False — платёж уже был в терминальном статусе (не PENDING) к моменту вызова
    (гонка с webhook/фоновой сверкой/повторное нажатие) — отмена не состоялась."""
    payment_id: int | None = None
    current_status: PaymentStatus | None = None
    """Актуальный статус на момент вызова — заполняется всегда, чтобы вызывающая
    сторона могла показать точное сообщение, даже при applied=False."""
    late_success_outcome: FinalizeOutcome | None = None
    """Заполняется, если в процессе отмены выяснилось, что банк уже подтвердил
    оплату (гонка: участник успел оплатить прямо в момент отмены) — платёж
    обработан через `finalize_payment(SUCCEEDED)` вместо отмены, банковский
    `Cancel` НЕ вызывался (иначе у Т-Банк это исполнилось бы как возврат денег).
    `late_success_outcome.applied` — удалось ли повторно выдать номерки;
    `late_success_outcome.late_success_no_tickets` — если нет, участника нужно
    уведомить отдельным сообщением (см. `notification_service`)."""


def cancel_payment(
    db: Database,
    provider: BasePaymentProvider,
    *,
    payment_id: int,
    now: dt.datetime | None = None,
) -> CancelOutcome:
    """Отменяет НЕОПЛАЧЕННЫЙ (`PENDING`) платёж — освобождает резерв номеров и
    закрывает платёжную сессию у банка. Это не возврат уже оплаченного платежа
    (см. ТЗ §21, DECISIONS.md) — работает только пока платёж ещё не оплачен.
    """
    now = now or utcnow()
    with db.immediate_session() as session:
        result = session.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == PaymentStatus.PENDING)
            .values(status=PaymentStatus.CANCELLED, cancelled_at=now)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            payment = session.get(Payment, payment_id)
            return CancelOutcome(
                applied=False,
                payment_id=payment_id,
                current_status=payment.status if payment is not None else None,
            )

        repo.release_reservation(session, payment_id=payment_id)
        payment = session.execute(select(Payment).where(Payment.id == payment_id)).scalar_one()
        order_id = payment.order_id
        external_payment_id = payment.external_payment_id

    # Сеть — вне транзакции (тот же принцип, что и в create_payment): не держим
    # write-lock SQLite на время сетевого вызова банку.
    try:
        bank_status = provider.cancel(order_id, external_payment_id=external_payment_id)
    except Exception:
        # Наша сторона уже авторитетно отменена (резерв снят) — сбой обращения к
        # банку не должен ломать отмену целиком, только логируется.
        logger.exception("payment_cancel_bank_call_failed", payment_id=payment_id)
        return CancelOutcome(
            applied=True, payment_id=payment_id, current_status=PaymentStatus.CANCELLED
        )

    if bank_status == PaymentStatus.SUCCEEDED:
        # Гонка: банк подтверждает, что платёж на самом деле уже оплачен — не
        # оставляем его в CANCELLED, прогоняем через ту же страховку от поздней
        # оплаты, что и webhook/фоновая сверка (см. _recover_late_success).
        recovery = finalize_payment(
            db, order_id=order_id, new_status=PaymentStatus.SUCCEEDED, now=now
        )
        return CancelOutcome(
            applied=False,
            payment_id=payment_id,
            current_status=PaymentStatus.SUCCEEDED,
            late_success_outcome=recovery,
        )

    return CancelOutcome(
        applied=True, payment_id=payment_id, current_status=PaymentStatus.CANCELLED
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
    помечается FAILED, резерв освобождается. Это должно срабатывать НЕЗАВИСИМО от
    того, ответил ли банк вообще: `check_status` может поднять исключение (сеть,
    ошибка банка, отсутствующий `external_payment_id` у старых платежей) — такой
    сбой трактуется как "статус пока не подтверждён", а не прерывает функцию
    целиком, иначе платёж мог бы зависнуть в PENDING навсегда, если банк
    постоянно недоступен (регресс, обнаруженный в проде — см. DECISIONS.md).
    """
    now = now or utcnow()
    with db.session() as session:
        payment = session.get(Payment, payment_id)
        if payment is None or payment.status != PaymentStatus.PENDING:
            return None
        order_id = payment.order_id
        created_at = payment.created_at
        attempts = payment.poll_attempts
        external_payment_id = payment.external_payment_id

    try:
        bank_status = provider.check_status(order_id, external_payment_id=external_payment_id)
    except Exception:
        logger.exception("payment_check_status_failed", payment_id=payment_id, order_id=order_id)
        bank_status = PaymentStatus.PENDING

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
