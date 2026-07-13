"""Раздел «Dashboard» — сводные показатели (п.11.4 ТЗ)."""

from __future__ import annotations

from app.core.permissions import Permission
from app.models.enums import PaymentStatus
from app.models.giveaway import Giveaway
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.ticket import Ticket
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.schemas import DashboardOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def get_dashboard(
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> DashboardOut:
    participants_count = session.execute(select(func.count()).select_from(Participant)).scalar_one()
    tickets_issued_count = session.execute(select(func.count()).select_from(Ticket)).scalar_one()
    giveaways_count = session.execute(select(func.count()).select_from(Giveaway)).scalar_one()
    revenue_total = session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.SUCCEEDED
        )
    ).scalar_one()
    return DashboardOut(
        participants_count=participants_count,
        tickets_issued_count=tickets_issued_count,
        revenue_total=revenue_total,
        giveaways_count=giveaways_count,
    )
