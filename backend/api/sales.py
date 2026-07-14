"""Раздел «Продажи» — список онлайн-платежей (п.11.4, 13, 14.2 ТЗ)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.core.permissions import Permission
from app.models.enums import PaymentProviderType, PaymentStatus
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from fastapi import APIRouter, Depends, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from backend.api.deps import get_session, require_permission
from backend.api.export_utils import ExportFormat, maybe_export
from backend.api.pagination import count_total, page_bounds, validate_page_size
from backend.api.schemas import PaymentOut

router = APIRouter(prefix="/payments", tags=["sales"])


def _to_dict(p: Payment) -> dict[str, Any]:
    return PaymentOut(
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


@router.get("", response_model=None)
def list_payments(
    giveaway_id: int | None = None,
    status_filter: PaymentStatus | None = None,
    order_id: str | None = None,
    provider: PaymentProviderType | None = None,
    participant_query: str | None = None,
    created_from: dt.date | None = None,
    created_to: dt.date | None = None,
    page: int = 1,
    page_size: int = 50,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.VIEW_SALES)),
) -> dict[str, Any] | list[dict[str, Any]] | Response:
    validate_page_size(page_size)
    stmt = select(Payment)
    if giveaway_id is not None:
        stmt = stmt.where(Payment.giveaway_id == giveaway_id)
    if status_filter is not None:
        stmt = stmt.where(Payment.status == status_filter)
    if order_id:
        stmt = stmt.where(Payment.order_id.like(f"%{order_id}%"))
    if provider is not None:
        stmt = stmt.where(Payment.provider == provider)
    if created_from is not None:
        stmt = stmt.where(Payment.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Payment.created_at < created_to + dt.timedelta(days=1))
    if participant_query:
        like = f"%{participant_query}%"
        stmt = stmt.join(Payment.participant).where(
            or_(Participant.phone.like(like), Participant.full_name.like(like))
        )
    # Уже сделали .join(Payment.participant) выше ради фильтра — populate из тех
    # же строк через contains_eager, а не отдельным joinedload (иначе два JOIN'а).
    participant_load = (
        contains_eager(Payment.participant)
        if participant_query
        else joinedload(Payment.participant)
    )

    if export is not None:
        eager_stmt = stmt.options(participant_load, joinedload(Payment.giveaway)).order_by(
            Payment.id.desc()
        )
        rows = [_to_dict(p) for p in session.execute(eager_stmt).scalars()]
        return maybe_export(rows, export, user, "sales", permission=Permission.SALES_EXPORT)

    total = count_total(session, stmt)
    limit, offset = page_bounds(page=page, page_size=page_size)
    eager_stmt = (
        stmt.options(participant_load, joinedload(Payment.giveaway))
        .order_by(Payment.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = session.execute(eager_stmt).scalars()
    return {
        "items": [_to_dict(p) for p in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
