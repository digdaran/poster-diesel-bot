"""Сверка входящих платежей по выписке расчётного счёта с неоплаченными счетами
`requisites_qr` (см. DECISIONS_LOG.md №38/39/51, ARCHITECTURE.md §3/§4).

Сопоставление — по назначению платежа (префикс розыгрыша + номер счёта,
`Giveaway.format_invoice_number` — `Giveaway.prefix` уникален по всей системе, см.
app/models/giveaway.py, поэтому номер счёта `PREFIX-NNNNN` тоже уникален глобально
и однозначно указывает на один `Payment`). Все операции с таким назначением в окне
выписки СУММИРУЮТСЯ (участник мог доплатить недостающее отдельным переводом по тому
же QR) и сравниваются с `Payment.amount`:

- сумма >= ожидаемой — счёт закрывается (`SUCCEEDED`); если пришло больше, чем нужно,
  разница дополнительно помечается на `Payment` (`amount_mismatch`/
  `amount_mismatch_bank_amount`) как переплата, для подсветки в панели «Продажи» —
  заказ при этом уже выполнен, возврат вне объёма ТЗ §21;
- сумма < ожидаемой — счёт остаётся `PENDING`, помечается тем же способом как
  недоплата (та же пара полей, разница в отображении — см. `SalesPage.tsx`) и
  логируется (`bank_statement_amount_mismatch`).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import structlog
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings
from app.core.db import Database
from app.models.bank_reconciliation_run import BankReconciliationRun
from app.models.base import utcnow
from app.models.enums import BankReconciliationRunStatus, PaymentProviderType, PaymentStatus
from app.models.payment import Payment
from app.payments.bank_statement import BankStatementEntry
from app.payments.tbank_statement import TBankStatementProvider
from app.services import payment_service as payment_svc
from app.services.payment_service import FinalizeOutcome

logger = structlog.get_logger(__name__)

# Обрезка error_message при сохранении BankReconciliationRun — сообщения исключений
# сетевого клиента иногда включают тело HTTP-ответа целиком, панели статуса
# достаточно превью.
_ERROR_MESSAGE_MAX_LEN = 2000


def find_matching_entries(
    entries: list[BankStatementEntry], invoice_no: str
) -> list[BankStatementEntry]:
    """Все операции в выписке, чьё назначение платежа указывает на этот счёт (по
    номеру), в порядке появления в выписке. Сумма считается отдельно в `reconcile()`
    — несколько частичных переводов по одному счёту суммируются, а не сопоставляются
    по одной точно совпавшей операции (см. DECISIONS_LOG.md №51)."""
    pattern = re.compile(r"№?\s*" + re.escape(invoice_no) + r"\b")
    return [entry for entry in entries if pattern.search(entry.purpose)]


def reconcile(
    db: Database, settings: Settings, *, now: dt.datetime | None = None
) -> list[FinalizeOutcome]:
    """Один тик фоновой сверки: находит совпадения по выписке и финализирует
    оплаченные счета, а также помечает FAILED неоплаченные счета старше TTL.

    Каждый вызов пишет строку `BankReconciliationRun` (панель статуса «Сверка выписок»
    в «Продажи», см. DECISIONS.md) — на ВСЕХ путях выхода, включая "нет кандидатов"
    (см. `run_started_at`/`run_status` ниже), чтобы панель могла отличить "нечего
    сверять" от "фоновый цикл не бежит"."""
    run_started_at = utcnow()
    now = now or run_started_at
    lookback_since = now - dt.timedelta(days=settings.bank_statement_lookback_days)
    ttl_cutoff = now - dt.timedelta(days=settings.requisites_invoice_ttl_days)

    with db.session() as session:
        candidates = list(
            session.execute(
                select(Payment)
                .options(joinedload(Payment.giveaway))
                .where(
                    Payment.provider == PaymentProviderType.REQUISITES_QR,
                    Payment.payment_number.is_not(None),
                    # PENDING — независимо от возраста (иначе неоплаченный счёт
                    # старше окна выписки никогда не дойдёт до проверки TTL, см.
                    # requisites_invoice_ttl_days, обычно ДЛИННЕЕ, чем
                    # bank_statement_lookback_days). CANCELLED/FAILED — только
                    # недавние (позднее восстановление, см. §4 ARCHITECTURE.md):
                    # искать совпадение по выписке для очень старой отменённой
                    # покупки бессмысленно (выписка всё равно за короткое окно).
                    or_(
                        Payment.status == PaymentStatus.PENDING,
                        and_(
                            Payment.status.in_((PaymentStatus.CANCELLED, PaymentStatus.FAILED)),
                            Payment.created_at >= lookback_since,
                        ),
                    ),
                )
            )
            .unique()
            .scalars()
            .all()
        )

    if not candidates:
        _record_run(
            db,
            settings,
            started_at=run_started_at,
            status=BankReconciliationRunStatus.SUCCESS,
            candidates_checked=0,
            entries_fetched=None,
        )
        return []

    try:
        entries = TBankStatementProvider.from_settings(settings).fetch_operations(
            since=lookback_since
        )
    except Exception as exc:
        logger.exception("bank_statement_fetch_failed")
        _record_run(
            db,
            settings,
            started_at=run_started_at,
            status=BankReconciliationRunStatus.FETCH_FAILED,
            candidates_checked=len(candidates),
            entries_fetched=None,
            error_message=str(exc),
        )
        return []

    outcomes: list[FinalizeOutcome] = []
    matched_count = 0
    mismatch_count = 0
    ttl_expired_count = 0
    finalize_error_count = 0
    for payment in candidates:
        invoice_no = payment.giveaway.format_invoice_number(payment.payment_number)  # type: ignore[arg-type]
        matching_entries = find_matching_entries(entries, invoice_no)
        if matching_entries:
            total_received = sum(entry.amount for entry in matching_entries)
            if total_received >= payment.amount:
                matched_count += 1
                try:
                    outcome = payment_svc.finalize_payment(
                        db,
                        order_id=payment.order_id,
                        new_status=PaymentStatus.SUCCEEDED,
                        raw_payload={
                            "bank_entries": [
                                {
                                    "external_id": entry.external_id,
                                    "amount": entry.amount,
                                    "purpose": entry.purpose,
                                    "operation_date": entry.operation_date.isoformat(),
                                }
                                for entry in matching_entries
                            ],
                            "total_received": total_received,
                        },
                        now=now,
                    )
                    if outcome.applied or outcome.late_success_no_tickets:
                        outcomes.append(outcome)
                        overpaid = total_received - payment.amount
                        if overpaid > 0:
                            # Заказ уже закрыт (номерки выданы) — переплата не
                            # блокирует финализацию, только подсвечивается оператору
                            # тем же полем, что и недоплата (см. DECISIONS_LOG.md
                            # №51); возврат разницы — вне объёма ТЗ §21.
                            _mark_amount_mismatch(
                                db, payment_id=payment.id, bank_amount=total_received
                            )
                except Exception:
                    logger.exception("bank_statement_finalize_failed", payment_id=payment.id)
                    finalize_error_count += 1
                continue

            mismatch_count += 1
            logger.warning(
                "bank_statement_amount_mismatch",
                invoice_no=invoice_no,
                expected_amount=payment.amount,
                actual_amount=total_received,
                entries_count=len(matching_entries),
            )
            _mark_amount_mismatch(db, payment_id=payment.id, bank_amount=total_received)
            # Деньги по этому счёту фактически идут (просто пока недостаточно) — не
            # даём TTL молча похоронить его как FAILED, пока сумма не наберётся или
            # расхождение не разберёт оператор вручную в панели (см. DECISIONS_LOG.md №39).
            continue

        if payment.status == PaymentStatus.PENDING and payment.created_at < ttl_cutoff:
            ttl_expired_count += 1
            try:
                outcome = payment_svc.finalize_payment(
                    db, order_id=payment.order_id, new_status=PaymentStatus.FAILED, now=now
                )
                if outcome.applied:
                    outcomes.append(outcome)
            except Exception:
                logger.exception("bank_statement_ttl_expire_failed", payment_id=payment.id)
                finalize_error_count += 1

    _record_run(
        db,
        settings,
        started_at=run_started_at,
        status=BankReconciliationRunStatus.SUCCESS,
        candidates_checked=len(candidates),
        entries_fetched=len(entries),
        matched_count=matched_count,
        mismatch_count=mismatch_count,
        ttl_expired_count=ttl_expired_count,
        finalize_error_count=finalize_error_count,
    )
    return outcomes


def _mark_amount_mismatch(db: Database, *, payment_id: int, bank_amount: int) -> None:
    try:
        with db.session() as session:
            session.execute(
                update(Payment)
                .where(Payment.id == payment_id)
                .values(amount_mismatch=True, amount_mismatch_bank_amount=bank_amount)
            )
    except Exception:
        logger.exception("bank_statement_mismatch_persist_failed", payment_id=payment_id)


def _record_run(
    db: Database,
    settings: Settings,
    *,
    started_at: dt.datetime,
    status: BankReconciliationRunStatus,
    candidates_checked: int,
    entries_fetched: int | None,
    matched_count: int = 0,
    mismatch_count: int = 0,
    ttl_expired_count: int = 0,
    finalize_error_count: int = 0,
    error_message: str | None = None,
) -> None:
    """Пишет строку статуса тика для панели «Сверка выписок» и заодно вычищает
    строки старше `bank_reconciliation_run_retention_days` — инлайн, без отдельного
    cron-а, т.к. тик короткий (по умолчанию раз в минуту) и без чистки таблица
    росла бы неограниченно."""
    finished_at = utcnow()
    try:
        with db.session() as session:
            session.add(
                BankReconciliationRun(
                    started_at=started_at,
                    finished_at=finished_at,
                    status=status,
                    candidates_checked=candidates_checked,
                    entries_fetched=entries_fetched,
                    matched_count=matched_count,
                    mismatch_count=mismatch_count,
                    ttl_expired_count=ttl_expired_count,
                    finalize_error_count=finalize_error_count,
                    error_message=(
                        error_message[:_ERROR_MESSAGE_MAX_LEN] if error_message else None
                    ),
                )
            )
            retention_cutoff = finished_at - dt.timedelta(
                days=settings.bank_reconciliation_run_retention_days
            )
            session.execute(
                delete(BankReconciliationRun).where(
                    BankReconciliationRun.started_at < retention_cutoff
                )
            )
    except Exception:
        logger.exception("bank_reconciliation_run_persist_failed")


@dataclass(frozen=True)
class BankReconciliationStatus:
    runs: list[BankReconciliationRun]
    """Последние тики, от новых к старым."""
    total_runs_24h: int
    failed_runs_24h: int
    last_success_at: dt.datetime | None
    is_stale: bool
    """Последний тик старше удвоенного интервала опроса — фоновый цикл, похоже,
    не бежит (а не просто "нечего сверять")."""


def get_reconciliation_status(
    db: Database, settings: Settings, *, now: dt.datetime | None = None, history_limit: int = 20
) -> BankReconciliationStatus:
    now = now or utcnow()
    window_start = now - dt.timedelta(hours=24)

    with db.session() as session:
        runs = list(
            session.execute(
                select(BankReconciliationRun)
                .order_by(BankReconciliationRun.started_at.desc())
                .limit(history_limit)
            )
            .scalars()
            .all()
        )
        total_runs_24h = session.execute(
            select(func.count())
            .select_from(BankReconciliationRun)
            .where(BankReconciliationRun.started_at >= window_start)
        ).scalar_one()
        failed_runs_24h = session.execute(
            select(func.count())
            .select_from(BankReconciliationRun)
            .where(
                BankReconciliationRun.started_at >= window_start,
                BankReconciliationRun.status == BankReconciliationRunStatus.FETCH_FAILED,
            )
        ).scalar_one()
        last_success_at = session.execute(
            select(func.max(BankReconciliationRun.started_at)).where(
                BankReconciliationRun.status == BankReconciliationRunStatus.SUCCESS
            )
        ).scalar_one_or_none()

    last_run = runs[0] if runs else None
    is_stale = last_run is None or (now - last_run.started_at) > dt.timedelta(
        seconds=2 * settings.online_status_poll_interval_sec
    )

    return BankReconciliationStatus(
        runs=runs,
        total_runs_24h=total_runs_24h,
        failed_runs_24h=failed_runs_24h,
        last_success_at=last_success_at,
        is_stale=is_stale,
    )


@dataclass(frozen=True)
class PaymentsCohortBrief:
    """Разбивка счетов `requisites_qr`, СОЗДАННЫХ за один календарный день (UTC),
    по текущему статусу — не по дню, в который статус изменился. Платёж, созданный
    сегодня и подтверждённый сегодня же, попадает в "сегодня"; если он провисит в
    PENDING неделю, он навсегда останется в когорте дня СОЗДАНИЯ, не "переедет"."""

    total_count: int
    total_amount: int
    succeeded_count: int
    """SUCCEEDED — деньги пришли и сверка их засчитала."""
    succeeded_amount: int
    pending_count: int
    """PENDING без активного расхождения суммы — ещё ждут поступления/сверки."""
    pending_amount: int
    disputed_count: int
    """PENDING с активным `amount_mismatch` — деньги пришли, но не той суммой,
    требуют ручного разбора оператором (см. DECISIONS_LOG.md №39)."""
    disputed_amount: int


@dataclass(frozen=True)
class PaymentsBrief:
    today: PaymentsCohortBrief
    yesterday: PaymentsCohortBrief


def _cohort_brief(
    session: Session, *, day_start: dt.datetime, day_end: dt.datetime
) -> PaymentsCohortBrief:
    rows = session.execute(
        select(Payment.status, Payment.amount_mismatch, Payment.amount).where(
            Payment.provider == PaymentProviderType.REQUISITES_QR,
            Payment.created_at >= day_start,
            Payment.created_at < day_end,
        )
    ).all()

    succeeded = [r for r in rows if r.status == PaymentStatus.SUCCEEDED]
    disputed = [r for r in rows if r.status == PaymentStatus.PENDING and r.amount_mismatch]
    pending = [r for r in rows if r.status == PaymentStatus.PENDING and not r.amount_mismatch]

    return PaymentsCohortBrief(
        total_count=len(rows),
        total_amount=sum(r.amount for r in rows),
        succeeded_count=len(succeeded),
        succeeded_amount=sum(r.amount for r in succeeded),
        pending_count=len(pending),
        pending_amount=sum(r.amount for r in pending),
        disputed_count=len(disputed),
        disputed_amount=sum(r.amount for r in disputed),
    )


def get_payments_brief(db: Database, *, now: dt.datetime | None = None) -> PaymentsBrief:
    """Краткая сводка по счетам `requisites_qr` за сегодня/вчера (UTC-сутки) для
    верхней строки статуса на «Продажи» — см. DECISIONS_LOG.md."""
    now = now or utcnow()
    today_start = dt.datetime.combine(now.date(), dt.time.min)
    yesterday_start = today_start - dt.timedelta(days=1)

    with db.session() as session:
        today = _cohort_brief(
            session, day_start=today_start, day_end=today_start + dt.timedelta(days=1)
        )
        yesterday = _cohort_brief(session, day_start=yesterday_start, day_end=today_start)

    return PaymentsBrief(today=today, yesterday=yesterday)
