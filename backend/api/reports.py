"""Раздел «Отчёты» (п.16 ТЗ). Просмотр и экспорт — Super Admin/Administrator."""

from __future__ import annotations

from typing import Any

from app.core.permissions import Permission
from app.models.panel_user import PanelUser
from app.services import report_service as svc
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend.api.deps import get_session, require_permission
from backend.api.export_utils import ExportFormat, maybe_export

router = APIRouter(prefix="/reports", tags=["reports"])


def _maybe_export(
    rows: list[dict[str, Any]], export: ExportFormat | None, user: PanelUser, filename: str
) -> list[dict[str, Any]] | Response:
    return maybe_export(rows, export, user, filename, permission=Permission.REPORTS_EXPORT)


@router.get("/sales-by-period", response_model=None)
def sales_by_period(
    granularity: str = "day",
    giveaway_id: int | None = None,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.REPORTS_VIEW)),
) -> list[dict[str, Any]] | Response:
    rows = svc.sales_by_period(session, granularity=granularity, giveaway_id=giveaway_id)
    return _maybe_export(rows, export, user, "sales_by_period")


@router.get("/online-vs-offline")
def online_vs_offline(
    giveaway_id: int | None = None,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.REPORTS_VIEW)),
) -> dict:
    return svc.online_vs_offline(session, giveaway_id=giveaway_id)


@router.get("/by-operator", response_model=None)
def by_operator(
    giveaway_id: int | None = None,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.REPORTS_VIEW)),
) -> list[dict[str, Any]] | Response:
    rows = svc.sales_by_operator(session, giveaway_id=giveaway_id)
    return _maybe_export(rows, export, user, "sales_by_operator")


@router.get("/by-provider", response_model=None)
def by_provider(
    giveaway_id: int | None = None,
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.REPORTS_VIEW)),
) -> list[dict[str, Any]] | Response:
    rows = svc.sales_by_provider(session, giveaway_id=giveaway_id)
    return _maybe_export(rows, export, user, "sales_by_provider")


@router.get("/tickets-by-source", response_model=None)
def tickets_by_source(
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.REPORTS_VIEW)),
) -> list[dict[str, Any]] | Response:
    rows = svc.tickets_by_giveaway_and_source(session)
    return _maybe_export(rows, export, user, "tickets_by_source")


@router.get("/participants", response_model=None)
def participants_report(
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.REPORTS_VIEW)),
) -> list[dict[str, Any]] | Response:
    rows = svc.participants_report(session)
    return _maybe_export(rows, export, user, "participants_report")


@router.get("/financial-summary")
def financial_summary(
    giveaway_id: int | None = None,
    session: Session = Depends(get_session),
    _user: PanelUser = Depends(require_permission(Permission.REPORTS_VIEW)),
) -> dict:
    return svc.financial_summary(session, giveaway_id=giveaway_id)


@router.get("/revenue-by-giveaway", response_model=None)
def revenue_by_giveaway(
    export: ExportFormat | None = None,
    session: Session = Depends(get_session),
    user: PanelUser = Depends(require_permission(Permission.REPORTS_VIEW)),
) -> list[dict[str, Any]] | Response:
    rows = svc.revenue_by_giveaway(session)
    return _maybe_export(rows, export, user, "revenue_by_giveaway")
