"""Раздел «Dashboard» — сводные показатели (п.11.4 ТЗ)."""

from __future__ import annotations

from app.core.permissions import Permission
from app.models.giveaway import Giveaway
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.ticket import Ticket
from app.services import report_service as svc
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
    revenue = svc.online_vs_offline(session)
    revenue_online = revenue["online"]["amount"]
    revenue_offline = revenue["offline"]["amount"]
    return DashboardOut(
        participants_count=participants_count,
        tickets_issued_count=tickets_issued_count,
        revenue_online=revenue_online,
        revenue_offline=revenue_offline,
        revenue_total=revenue_online + revenue_offline,
        giveaways_count=giveaways_count,
    )
