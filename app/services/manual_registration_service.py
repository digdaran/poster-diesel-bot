"""Сервис ручных (офлайн) регистраций (п.7.5, 7.7, 8.2 ТЗ). Единственная точка
входа для бизнес-логики офлайн-продаж — API панели/бот-оператор вызывают только
эти функции.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select, update

from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import ManualRegistrationPaymentMethod, ManualRegistrationStatus, TicketSource
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.ticket import Ticket
from app.payments import qr_requisites as qr
from app.payments.requisites_qr import RequisitesQrProvider
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
    DECISIONS_LOG.md №45), а не нехватки номеров."""
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
class GenerateQrOutcome:
    manual_registration_id: int
    invoice_no: str
    qr_code_payload: str
    amount: int
    """Сумма в копейках (quantity * ticket_price) — для отображения оператору рядом с QR."""


def generate_manual_registration_qr(
    db: Database,
    settings: Settings,
    *,
    manual_registration_id: int,
    now: dt.datetime | None = None,
) -> GenerateQrOutcome:
    """Формирует QR с банковскими реквизитами для оплаты конкретной ручной
    регистрации — по желанию покупателя, вместо наличных оператору в кассу (см.
    DECISIONS.md). Доступно только для `PENDING`; присваивает номер счёта и
    собирает payload теми же чистыми функциями (`app.payments.qr_requisites`),
    что и `RequisitesQrProvider` для онлайн-оплаты.

    Продлевает резерв номерков в пуле до `REQUISITES_INVOICE_TTL_DAYS` — того же
    TTL, что и у онлайн-счетов по QR (обычно дни, а не `MANUAL_RESERVATION_TTL_SEC`
    ~час, рассчитанный на "покупатель стоит у оператора"): банковский перевод по
    QR может дойти не сразу, а короткий TTL молча освободил бы номерки раньше,
    чем покупатель успеет заплатить (см. DECISIONS.md). Обратное продление —
    `switch_manual_registration_to_cash`.

    Идемпотентно: повторный вызов для той же регистрации возвращает уже
    сформированные ранее номер счёта и payload, не трогая счётчик
    `Giveaway.next_payment_number` повторно (TTL продлевается заново).
    """
    now = now or utcnow()
    with db.immediate_session() as session:
        registration = session.execute(
            select(ManualRegistration).where(ManualRegistration.id == manual_registration_id)
        ).scalar_one_or_none()
        if registration is None:
            raise ManualRegistrationStateError(f"Регистрация {manual_registration_id} не найдена")
        if registration.status != ManualRegistrationStatus.PENDING:
            raise ManualRegistrationStateError(
                f"Регистрация {manual_registration_id} в статусе {registration.status} — "
                "QR можно сформировать только для регистрации в статусе PENDING"
            )

        giveaway = session.execute(
            select(Giveaway).where(Giveaway.id == registration.giveaway_id)
        ).scalar_one()
        amount = registration.quantity * giveaway.ticket_price
        qr_reserved_until = now + dt.timedelta(days=settings.requisites_invoice_ttl_days)

        if registration.payment_number is not None:
            assert registration.qr_code_payload is not None  # заполняются вместе, см. ниже
            repo.extend_reservation(
                session,
                manual_registration_id=manual_registration_id,
                reserved_until=qr_reserved_until,
            )
            return GenerateQrOutcome(
                manual_registration_id=manual_registration_id,
                invoice_no=giveaway.format_invoice_number(registration.payment_number),
                qr_code_payload=registration.qr_code_payload,
                amount=amount,
            )

        payment_number = giveaway.next_payment_number
        session.execute(
            update(Giveaway)
            .where(Giveaway.id == giveaway.id)
            .values(next_payment_number=Giveaway.next_payment_number + 1)
        )
        invoice_no = giveaway.format_invoice_number(payment_number)

        provider = RequisitesQrProvider.from_settings(settings)
        purpose = qr.build_purpose(
            invoice_no=invoice_no,
            invoice_date=now.date(),
            amount_kopecks=amount,
            vat_rate_percent=provider.vat_rate_percent,
        )
        payload = qr.build_st00012_payload(
            name=provider.recipient_name,
            personal_acc=provider.personal_acc,
            bank_name=provider.bank_name,
            bic=provider.bic,
            corresp_acc=provider.corresp_acc,
            payee_inn=provider.recipient_inn,
            kpp=provider.recipient_kpp or None,
            sum_kopecks=amount,
            purpose=purpose,
        )

        session.execute(
            update(ManualRegistration)
            .where(ManualRegistration.id == manual_registration_id)
            .values(
                payment_method=ManualRegistrationPaymentMethod.CASHLESS,
                payment_number=payment_number,
                qr_code_payload=payload,
                qr_generated_at=now,
            )
        )
        repo.extend_reservation(
            session, manual_registration_id=manual_registration_id, reserved_until=qr_reserved_until
        )

        return GenerateQrOutcome(
            manual_registration_id=manual_registration_id,
            invoice_no=invoice_no,
            qr_code_payload=payload,
            amount=amount,
        )


def switch_manual_registration_to_cash(
    db: Database,
    settings: Settings,
    *,
    manual_registration_id: int,
    now: dt.datetime | None = None,
) -> None:
    """Возврат к наличным после того, как для регистрации уже был сформирован
    QR (`payment_method=CASHLESS`), но покупатель не смог/не захотел оплатить
    по QR и решил заплатить оператору наличными (см. DECISIONS.md). Только для
    `PENDING`. Не трогает уже присвоенный номер счёта/QR-payload — они просто
    остаются неиспользованными (как и у отменённого онлайн-платежа); достаточно
    сменить `payment_method`, из-за чего регистрация перестаёт быть кандидатом
    фоновой сверки выписки (`bank_reconciliation_service.reconcile` матчит
    только `CASHLESS`).

    Также укорачивает TTL резерва в пуле обратно до обычного "наличного" окна
    (`MANUAL_RESERVATION_TTL_SEC`) — иначе номерки остались бы заморожены на
    весь долгий QR-TTL (`generate_manual_registration_qr` продлевает его до
    `REQUISITES_INVOICE_TTL_DAYS`), хотя оплата наличными подразумевает, что
    покупатель платит прямо сейчас у оператора, а не когда-то в течение дней.
    """
    now = now or utcnow()
    with db.immediate_session() as session:
        result = session.execute(
            update(ManualRegistration)
            .where(
                ManualRegistration.id == manual_registration_id,
                ManualRegistration.status == ManualRegistrationStatus.PENDING,
            )
            .values(payment_method=ManualRegistrationPaymentMethod.CASH)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            registration = session.get(ManualRegistration, manual_registration_id)
            if registration is None:
                raise ManualRegistrationStateError(
                    f"Регистрация {manual_registration_id} не найдена"
                )
            raise ManualRegistrationStateError(
                f"Регистрация {manual_registration_id} в статусе {registration.status} — "
                "сменить способ оплаты можно только для PENDING"
            )
        repo.extend_reservation(
            session,
            manual_registration_id=manual_registration_id,
            reserved_until=now + dt.timedelta(seconds=settings.manual_reservation_ttl_sec),
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
