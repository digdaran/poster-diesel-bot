"""BankReconciliationRun — статус одного тика фоновой сверки банковской выписки
(`app/services/bank_reconciliation_service.py::reconcile()`). Пишется на КАЖДОМ тике,
включая случай "нет неоплаченных счетов requisites_qr" (запрос к банку не делался) —
иначе панель не отличит "нечего сверять" от "фоновый цикл упал/не запущен". См.
DECISIONS_LOG.md."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import BankReconciliationRunStatus


class BankReconciliationRun(Base):
    __tablename__ = "bank_reconciliation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[BankReconciliationRunStatus] = mapped_column(
        SAEnum(BankReconciliationRunStatus, native_enum=False), nullable=False
    )
    candidates_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # None — запрос к банку не делался (не было неоплаченных счетов requisites_qr).
    entries_fetched: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ttl_expired_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Индивидуальные finalize_payment/TTL-обработки, упавшие исключением внутри тика
    # (сейчас только логируются bank_statement_finalize_failed/bank_statement_ttl_expire_failed
    # и терялись бы для панели без этого счётчика).
    finalize_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
