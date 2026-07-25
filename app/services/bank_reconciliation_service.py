"""Сверка входящих платежей по выписке расчётного счёта с неоплаченными счетами
`requisites_qr` (см. DECISIONS_LOG.md №38/39, ARCHITECTURE.md §3/§4).

Сопоставление — по назначению платежа (префикс розыгрыша + номер счёта,
`Giveaway.format_invoice_number` — `Giveaway.prefix` уникален по всей системе, см.
app/models/giveaway.py, поэтому номер счёта `PREFIX-NNNNN` тоже уникален глобально
и однозначно указывает на один `Payment`) И точному совпадению суммы операции с
`Payment.amount`. Операция с совпавшим назначением, но другой суммой, счёт не
закрывает (остаётся PENDING до ручной проверки/TTL) — вместо этого помечается на
`Payment` (`amount_mismatch`/`amount_mismatch_bank_amount`) для подсветки в панели
(«Продажи») и логируется (`bank_statement_amount_mismatch`).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import structlog
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import joinedload

from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import PaymentProviderType, PaymentStatus
from app.models.payment import Payment
from app.payments.bank_statement import BankStatementEntry
from app.payments.tbank_statement import TBankStatementProvider
from app.services import payment_service as payment_svc
from app.services.payment_service import FinalizeOutcome

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class InvoiceMatchResult:
    matched: BankStatementEntry | None
    """Точное совпадение — назначение платежа И сумма. Готово к финализации."""
    mismatched: BankStatementEntry | None
    """Назначение платежа совпало, сумма — нет (первая такая операция в выписке).
    Не финализирует счёт — только повод подсветить его в панели оператору."""


def find_matching_entry(
    entries: list[BankStatementEntry], invoice_no: str, expected_amount: int
) -> InvoiceMatchResult:
    """Совпадение по номеру счёта в назначении платежа И точной сумме. Операция
    с верным назначением, но другой суммой, не считается совпадением (не должна
    закрывать счёт по неполной/избыточной оплате) — возвращается отдельно
    (`mismatched`) для сохранения на `Payment`, чтобы оператор увидел расхождение
    в панели."""
    pattern = re.compile(r"№?\s*" + re.escape(invoice_no) + r"\b")
    mismatched: BankStatementEntry | None = None
    for entry in entries:
        if not pattern.search(entry.purpose):
            continue
        if entry.amount == expected_amount:
            return InvoiceMatchResult(matched=entry, mismatched=None)
        if mismatched is None:
            mismatched = entry
        logger.warning(
            "bank_statement_amount_mismatch",
            invoice_no=invoice_no,
            expected_amount=expected_amount,
            actual_amount=entry.amount,
            external_id=entry.external_id,
        )
    return InvoiceMatchResult(matched=None, mismatched=mismatched)


def reconcile(
    db: Database, settings: Settings, *, now: dt.datetime | None = None
) -> list[FinalizeOutcome]:
    """Один тик фоновой сверки: находит совпадения по выписке и финализирует
    оплаченные счета, а также помечает FAILED неоплаченные счета старше TTL."""
    now = now or utcnow()
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
        return []

    try:
        entries = TBankStatementProvider.from_settings(settings).fetch_operations(
            since=lookback_since
        )
    except Exception:
        logger.exception("bank_statement_fetch_failed")
        return []

    outcomes: list[FinalizeOutcome] = []
    for payment in candidates:
        invoice_no = payment.giveaway.format_invoice_number(payment.payment_number)  # type: ignore[arg-type]
        result = find_matching_entry(entries, invoice_no, payment.amount)
        if result.matched is not None:
            try:
                outcome = payment_svc.finalize_payment(
                    db,
                    order_id=payment.order_id,
                    new_status=PaymentStatus.SUCCEEDED,
                    raw_payload={
                        "bank_entry": {
                            "external_id": result.matched.external_id,
                            "amount": result.matched.amount,
                            "purpose": result.matched.purpose,
                            "operation_date": result.matched.operation_date.isoformat(),
                        }
                    },
                    now=now,
                )
                if outcome.applied or outcome.late_success_no_tickets:
                    outcomes.append(outcome)
            except Exception:
                logger.exception("bank_statement_finalize_failed", payment_id=payment.id)
            continue

        if result.mismatched is not None:
            _mark_amount_mismatch(db, payment_id=payment.id, bank_amount=result.mismatched.amount)
            # Деньги по этому счёту фактически идут (просто не той суммой) — не
            # даём TTL молча похоронить его как FAILED, пока расхождение не
            # разобрано оператором вручную в панели (см. DECISIONS_LOG.md №39).
            continue

        if payment.status == PaymentStatus.PENDING and payment.created_at < ttl_cutoff:
            try:
                outcome = payment_svc.finalize_payment(
                    db, order_id=payment.order_id, new_status=PaymentStatus.FAILED, now=now
                )
                if outcome.applied:
                    outcomes.append(outcome)
            except Exception:
                logger.exception("bank_statement_ttl_expire_failed", payment_id=payment.id)

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
