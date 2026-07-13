"""Раздел «Продажи» — список онлайн-платежей (п.11.4, 13, 14.2 ТЗ)."""

from __future__ import annotations

from app.core.permissions import Permission
from app.models.enums import PaymentStatus
from app.models.panel_user import PanelUser
from app.models.payment import Payment
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.schemas import PaymentOut

router = APIRouter(prefix="/payments", tags=["sales"])


@router.get("", response_model=list[PaymentOut])
def list_payments(
    giveaway_id: int | None = None,
    status_filter: PaymentStatus | None = None,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_SALES)),
) -> list[Payment]:
    stmt = select(Payment).order_by(Payment.id.desc())
    if giveaway_id is not None:
        stmt = stmt.where(Payment.giveaway_id == giveaway_id)
    if status_filter is not None:
        stmt = stmt.where(Payment.status == status_filter)
    return list(session.execute(stmt).scalars())
