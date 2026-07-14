"""Общая помощь для CSV/XLSX-экспорта списков панели (п.16 ТЗ)."""

from __future__ import annotations

from typing import Any, Literal

from app.core.permissions import PanelRole, Permission, role_has_permission
from app.models.panel_user import PanelUser
from app.services import report_service as svc
from fastapi import HTTPException, Response, status

ExportFormat = Literal["csv", "xlsx"]


def maybe_export(
    rows: list[dict[str, Any]],
    export: ExportFormat | None,
    user: PanelUser,
    filename: str,
    *,
    permission: Permission,
) -> list[dict[str, Any]] | Response:
    if export is None:
        return rows
    role = PanelRole(user.role.value)
    if not role_has_permission(role, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для экспорта"
        )
    if export == "csv":
        content = svc.to_csv(rows)
        media_type = "text/csv"
    else:
        content = svc.to_xlsx(rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}.{export}"'},
    )
