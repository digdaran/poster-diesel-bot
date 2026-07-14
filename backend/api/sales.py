"""Раздел «Продажи» — список онлайн-платежей (п.11.4, 13, 14.2 ТЗ)."""

from __future__ import annotations

from typing import Any

from app.core.permissions import Permission
from app.models.enums import PaymentStatus
from app.models.panel_user import PanelUser
from app.models.payment import Payment
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.api.deps import get_session, require_permission
from backend.api.export_utils import ExportFormat, maybe_export
from backend.api.schemas import PaymentOut

router = APIRouter(prefix="/payments", tags=["sales"])


@router.get("", response_model=None)
def list_payments(
    giveaway_id: int | None = None,
    status_filter: PaymentStatus | None = None,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.VIEW_SALES)),
) -> list[dict[str, Any]] | Response:
    stmt = (
        select(Payment)
        .options(joinedload(Payment.participant), joinedload(Payment.giveaway))
        .order_by(Payment.id.desc())
    )
    if giveaway_id is not None:
        stmt = stmt.where(Payment.giveaway_id == giveaway_id)
    if status_filter is not None:
        stmt = stmt.where(Payment.status == status_filter)
    payments = session.execute(stmt).scalars()

    rows = [
        PaymentOut(
            id=p.id,
            order_id=p.order_id,
            participant_id=p.participant_id,
            participant_phone=p.participant.phone,
            participant_full_name=p.participant.full_name,
            giveaway_id=p.giveaway_id,
            giveaway_name=p.giveaway.name,
            provider=p.provider.value,
            amount=p.amount,
            quantity=p.quantity,
            status=p.status.value,
            created_at=p.created_at,
            confirmed_at=p.confirmed_at,
        ).model_dump(mode="json")
        for p in payments
    ]
    return maybe_export(rows, export, user, "sales", permission=Permission.SALES_EXPORT)
