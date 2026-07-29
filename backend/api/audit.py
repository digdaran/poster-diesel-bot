"""Раздел «Журнал аудита» — просмотр (append-only, п.17 ТЗ). Super Admin/Administrator."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.core.permissions import Permission
from app.models.audit_log import AuditLog
from app.models.panel_user import PanelUser
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.pagination import count_total, page_bounds, validate_page_size
from backend.api.schemas import AuditLogOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=None)
def list_audit_log(
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor_query: str | None = None,
    ip_address: str | None = None,
    created_from: dt.date | None = None,
    created_to: dt.date | None = None,
    page: int = 1,
    page_size: int = 50,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.AUDIT_VIEW)),
) -> dict[str, Any]:
    validate_page_size(page_size)
    stmt = select(AuditLog)
    if action:
        # Подстрокой, не точным совпадением: действий много (см. audit_service.log
        # вызовы по всему коду) и список постоянно растёт — точный список в
        # выпадашке фронта отставал бы так же, как и до этой доработки.
        stmt = stmt.where(AuditLog.action.like(f"%{action}%"))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_query:
        stmt = stmt.where(AuditLog.actor_label.like(f"%{actor_query}%"))
    if ip_address:
        stmt = stmt.where(AuditLog.ip_address.like(f"%{ip_address}%"))
    if created_from is not None:
        stmt = stmt.where(AuditLog.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(AuditLog.created_at < created_to + dt.timedelta(days=1))

    total = count_total(session, stmt)
    limit, offset = page_bounds(page=page, page_size=page_size)
    page_stmt = stmt.order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    items = session.execute(page_stmt).scalars().all()
    return {
        "items": [AuditLogOut.model_validate(e).model_dump(mode="json") for e in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
