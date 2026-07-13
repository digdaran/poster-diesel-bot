"""Раздел «Журнал аудита» — просмотр (append-only, п.17 ТЗ). Super Admin/Administrator."""

from __future__ import annotations

from app.core.permissions import Permission
from app.models.audit_log import AuditLog
from app.models.panel_user import PanelUser
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.schemas import AuditLogOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
def list_audit_log(
    action: str | None = None,
    entity_type: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.AUDIT_VIEW)),
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    return list(session.execute(stmt).scalars())
