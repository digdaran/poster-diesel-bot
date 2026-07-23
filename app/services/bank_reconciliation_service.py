"""Сверка входящих платежей по выписке расчётного счёта с неоплаченными счетами
`requisites_qr` (см. DECISIONS.md, ARCHITECTURE.md §3/§4).

Сопоставление — только по назначению платежа: префикс розыгрыша + номер счёта
(`Giveaway.format_invoice_number`), без сверки суммы (по прямому запросу
заказчика) — `Giveaway.prefix` уникален по всей системе (см. app/models/giveaway.py),
поэтому номер счёта `PREFIX-NNNNN` тоже уникален глобально и однозначно указывает
на один `Payment`.
"""

from __future__ import annotations

import datetime as dt
import re

import structlog
from sqlalchemy import and_, or_, select
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


def find_matching_entry(
    entries: list[BankStatementEntry], invoice_no: str
) -> BankStatementEntry | None:
    pattern = re.compile(r"№?\s*" + re.escape(invoice_no) + r"\b")
    for entry in entries:
        if pattern.search(entry.purpose):
            return entry
    return None


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
        match = find_matching_entry(entries, invoice_no)
        if match is not None:
            try:
                outcome = payment_svc.finalize_payment(
                    db,
                    order_id=payment.order_id,
                    new_status=PaymentStatus.SUCCEEDED,
                    raw_payload={
                        "bank_entry": {
                            "external_id": match.external_id,
                            "amount": match.amount,
                            "purpose": match.purpose,
                            "operation_date": match.operation_date.isoformat(),
                        }
                    },
                    now=now,
                )
                if outcome.applied or outcome.late_success_no_tickets:
                    outcomes.append(outcome)
            except Exception:
                logger.exception("bank_statement_finalize_failed", payment_id=payment.id)
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
