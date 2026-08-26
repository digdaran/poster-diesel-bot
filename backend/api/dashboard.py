"""Раздел «Dashboard» — сводные показатели (п.11.4 ТЗ), карточки по коллекциям
и операционные алерты (см. app/services/dashboard_service.py)."""

from __future__ import annotations

import datetime as dt

from app.core.config import Settings
from app.core.permissions import Permission
from app.models.base import utcnow
from app.models.giveaway import Giveaway
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.ticket import Ticket
from app.services import dashboard_service
from app.services import report_service as svc
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_session, get_settings_dep, require_permission
from backend.api.schemas import (
    ChannelSalesOut,
    DashboardAlertOut,
    DashboardGiveawayCardOut,
    DashboardOut,
    DashboardSalesPointOut,
    ManualFunnelOut,
    OnlineFunnelOut,
    SalesVelocityOut,
    TopParticipantOut,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Ширина окна графика "общая динамика продаж" на Dashboard — фиксированная, без
# селектора диапазона/гранулярности (это сводка для беглого взгляда, за более
# гибкой аналитикой — на «Отчёты», см. обсуждение с владельцем при внедрении).
SALES_TREND_DAYS = 30


@router.get("", response_model=DashboardOut)
def get_dashboard(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    _user: PanelUser = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> DashboardOut:
    participants_count = session.execute(select(func.count()).select_from(Participant)).scalar_one()
    tickets_issued_count = session.execute(select(func.count()).select_from(Ticket)).scalar_one()
    giveaways_count = session.execute(select(func.count()).select_from(Giveaway)).scalar_one()
    revenue = svc.online_vs_offline(session)
    revenue_online = revenue["online"]["amount"]
    revenue_offline = revenue["offline"]["amount"]
    average_check = (
        revenue["online"]["amount"] // revenue["online"]["count"]
        if revenue["online"]["count"]
        else 0
    )

    trend_date_to = utcnow().date()
    trend_date_from = trend_date_to - dt.timedelta(days=SALES_TREND_DAYS - 1)
    sales_trend = svc.sales_by_period(
        session, granularity="day", date_from=trend_date_from, date_to=trend_date_to
    )

    # Тот же онлайн-график, что и sales_trend, но за предыдущие SALES_TREND_DAYS
    # дней — только чтобы посчитать сумму для дельты у заголовка графика
    # ("+12% к прошлым 30 дням"), сами точки на фронт не идут.
    prev_trend_date_to = trend_date_from - dt.timedelta(days=1)
    prev_trend_date_from = prev_trend_date_to - dt.timedelta(days=SALES_TREND_DAYS - 1)
    sales_trend_prev_total = sum(
        row["amount"]
        for row in svc.sales_by_period(
            session, granularity="day", date_from=prev_trend_date_from, date_to=prev_trend_date_to
        )
    )

    # Выручка (онлайн + офлайн, как revenue_total) за сегодня и за вчера целиком
    # — для дельты на hero-карточке. "Сегодня" — текущий, ещё не завершившийся
    # день, сравнение с целым "вчера" честно предупреждено на фронте подписью,
    # а не выдаётся за сравнение сопоставимых периодов.
    today = trend_date_to
    yesterday = today - dt.timedelta(days=1)
    daily_online = {
        row["period"]: row["amount"]
        for row in svc.sales_by_period(
            session, granularity="day", date_from=yesterday, date_to=today
        )
    }
    daily_offline = svc.offline_revenue_by_day(session, date_from=yesterday, date_to=today)

    def _day_total(d: dt.date) -> int:
        key = d.strftime("%Y-%m-%d")
        return daily_online.get(key, 0) + daily_offline.get(key, 0)

    velocity = dashboard_service.sales_velocity_last_hour(session)
    top_participants = dashboard_service.top_participants_by_revenue(session)
    funnel_online, funnel_manual = dashboard_service.sales_funnel(session)

    return DashboardOut(
        participants_count=participants_count,
        tickets_issued_count=tickets_issued_count,
        revenue_online=revenue_online,
        revenue_offline=revenue_offline,
        revenue_total=revenue_online + revenue_offline,
        giveaways_count=giveaways_count,
        giveaways=[
            DashboardGiveawayCardOut.model_validate(card)
            for card in dashboard_service.giveaway_cards(session)
        ],
        sales_trend=[DashboardSalesPointOut(**row) for row in sales_trend],
        alerts=[
            DashboardAlertOut.model_validate(alert)
            for alert in dashboard_service.compute_alerts(session, settings)
        ],
        average_check=average_check,
        revenue_by_channel=[ChannelSalesOut(**row) for row in svc.sales_by_channel(session)],
        sales_trend_prev_total=sales_trend_prev_total,
        revenue_today=_day_total(today),
        revenue_yesterday=_day_total(yesterday),
        sales_velocity_last_hour=SalesVelocityOut.model_validate(velocity),
        top_participants=[TopParticipantOut.model_validate(p) for p in top_participants],
        funnel_online=OnlineFunnelOut.model_validate(funnel_online),
        funnel_manual=ManualFunnelOut.model_validate(funnel_manual),
    )
