"""Раздел «Номерки» (п.11.4, 14.2 ТЗ)."""

from __future__ import annotations

from typing import Any

from app.core.permissions import Permission
from app.models.panel_user import PanelUser
from app.models.ticket import Ticket
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.api.deps import get_session, require_permission
from backend.api.export_utils import ExportFormat, maybe_export
from backend.api.schemas import TicketOut

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=None)
def list_tickets(
    giveaway_id: int | None = None,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.VIEW_TICKETS)),
) -> list[dict[str, Any]] | Response:
    stmt = (
        select(Ticket)
        .options(joinedload(Ticket.participant), joinedload(Ticket.giveaway))
        .order_by(Ticket.id.desc())
    )
    if giveaway_id is not None:
        stmt = stmt.where(Ticket.giveaway_id == giveaway_id)
    tickets = session.execute(stmt).scalars()

    rows = [
        TicketOut(
            id=t.id,
            giveaway_id=t.giveaway_id,
            giveaway_name=t.giveaway.name,
            number=t.number,
            full_code=t.full_code,
            participant_id=t.participant_id,
            participant_phone=t.participant.phone,
            participant_full_name=t.participant.full_name,
            source=t.source.value,
            created_at=t.created_at,
        ).model_dump(mode="json")
        for t in tickets
    ]
    return maybe_export(rows, export, user, "tickets", permission=Permission.SALES_EXPORT)
