"""Статус фоновой сверки банковской выписки (`app/services/bank_reconciliation_service.py`)
для панели «Продажи» — блок «Сверка выписок», доступен только Administrator/Super Admin
(см. DECISIONS.md)."""

from __future__ import annotations

from app.core.config import Settings
from app.core.db import Database
from app.core.permissions import Permission
from app.models.panel_user import PanelUser
from app.services import bank_reconciliation_service as reconciliation_svc
from fastapi import APIRouter, Depends

from backend.api.deps import get_database, get_settings_dep, require_permission
from backend.api.schemas import (
    BankReconciliationRunOut,
    BankReconciliationStatusOut,
    PaymentsBriefOut,
)

router = APIRouter(prefix="/bank-reconciliation", tags=["bank-reconciliation"])


@router.get("/status", response_model=BankReconciliationStatusOut)
def get_status(
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings_dep),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_BANK_RECONCILIATION)),
) -> BankReconciliationStatusOut:
    status_ = reconciliation_svc.get_reconciliation_status(db, settings)
    brief = reconciliation_svc.get_payments_brief(db)
    return BankReconciliationStatusOut(
        runs=[BankReconciliationRunOut.model_validate(run) for run in status_.runs],
        total_runs_24h=status_.total_runs_24h,
        failed_runs_24h=status_.failed_runs_24h,
        last_success_at=status_.last_success_at,
        is_stale=status_.is_stale,
        payments_brief=PaymentsBriefOut.model_validate(brief),
    )
