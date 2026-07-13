"""Раздел «Номерки» (п.11.4, 14.2 ТЗ)."""

from __future__ import annotations

from app.core.permissions import Permission
from app.models.panel_user import PanelUser
from app.models.ticket import Ticket
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.schemas import TicketOut

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=list[TicketOut])
def list_tickets(
    giveaway_id: int | None = None,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_TICKETS)),
) -> list[Ticket]:
    stmt = select(Ticket).order_by(Ticket.id.desc())
    if giveaway_id is not None:
        stmt = stmt.where(Ticket.giveaway_id == giveaway_id)
    return list(session.execute(stmt).scalars())
