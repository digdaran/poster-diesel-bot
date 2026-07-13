"""Журнал аудита (п.6.2, 17 ТЗ). Append-only: только создание записей, никаких
update/delete не выставляется наружу ни здесь, ни в API."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.enums import AuditActorType


def log(
    session: Session,
    *,
    action: str,
    actor_type: AuditActorType,
    actor_label: str,
    actor_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
        ip_address=ip_address,
    )
    session.add(entry)
    session.flush()
    return entry
