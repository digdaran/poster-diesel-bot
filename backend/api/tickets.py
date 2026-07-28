"""Раздел «Номерки» (п.11.4, 14.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.core.permissions import Permission
from app.models.enums import ChannelType, TicketSource
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.ticket import Ticket
from fastapi import APIRouter, Depends, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from backend.api.deps import get_session, require_permission
from backend.api.export_utils import ExportFormat, maybe_export
from backend.api.pagination import count_total, page_bounds, validate_page_size
from backend.api.schemas import TicketOut

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _to_dict(t: Ticket) -> dict[str, Any]:
    return TicketOut(
        id=t.id,
        giveaway_id=t.giveaway_id,
        giveaway_name=t.giveaway.name,
        number=t.number,
        full_code=t.full_code,
        participant_id=t.participant_id,
        participant_phone=t.participant.phone,
        participant_full_name=t.participant.full_name,
        source=t.source.value,
        channel=t.payment.channel.value if t.payment and t.payment.channel else None,
        created_at=t.created_at,
    ).model_dump(mode="json")


@router.get("", response_model=None)
def list_tickets(
    giveaway_id: int | None = None,
    full_code: str | None = None,
    participant_id: int | None = None,
    participant_query: str | None = None,
    source: TicketSource | None = None,
    channel: ChannelType | None = None,
    manual_registration_id: int | None = None,
    created_from: dt.date | None = None,
    created_to: dt.date | None = None,
    page: int = 1,
    page_size: int = 50,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.VIEW_TICKETS)),
) -> dict[str, Any] | list[dict[str, Any]] | Response:
    validate_page_size(page_size)
    stmt = select(Ticket)
    if giveaway_id is not None:
        stmt = stmt.where(Ticket.giveaway_id == giveaway_id)
    if manual_registration_id is not None:
        stmt = stmt.where(Ticket.manual_registration_id == manual_registration_id)
    if full_code:
        stmt = stmt.where(Ticket.full_code.like(f"%{full_code}%"))
    if participant_id is not None:
        stmt = stmt.where(Ticket.participant_id == participant_id)
    if source is not None:
        stmt = stmt.where(Ticket.source == source)
    if channel is not None:
        stmt = stmt.join(Ticket.payment).where(Payment.channel == channel)
    if created_from is not None:
        stmt = stmt.where(Ticket.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Ticket.created_at < created_to + dt.timedelta(days=1))
    if participant_query:
        like = f"%{participant_query}%"
        stmt = stmt.join(Ticket.participant).where(
            or_(Participant.phone.like(like), Participant.full_name.like(like))
        )
    participant_load = (
        contains_eager(Ticket.participant) if participant_query else joinedload(Ticket.participant)
    )
    payment_load = (
        contains_eager(Ticket.payment) if channel is not None else joinedload(Ticket.payment)
    )

    if export is not None:
        eager_stmt = stmt.options(
            participant_load, joinedload(Ticket.giveaway), payment_load
        ).order_by(Ticket.id.desc())
        rows = [_to_dict(t) for t in session.execute(eager_stmt).scalars()]
        return maybe_export(rows, export, user, "tickets", permission=Permission.SALES_EXPORT)

    total = count_total(session, stmt)
    limit, offset = page_bounds(page=page, page_size=page_size)
    eager_stmt = (
        stmt.options(participant_load, joinedload(Ticket.giveaway), payment_load)
        .order_by(Ticket.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = session.execute(eager_stmt).scalars()
    return {
        "items": [_to_dict(t) for t in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
