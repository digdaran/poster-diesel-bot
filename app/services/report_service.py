"""Отчёты (п.16 ТЗ): продажи по периодам, онлайн/офлайн, по операторам, по
провайдерам, номерки по розыгрышам/источнику, по участникам, финансовая сводка.
Экспорт — CSV (`;`, UTF-8 BOM) и XLSX (openpyxl).
"""

from __future__ import annotations

import csv
import io
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import ManualRegistrationStatus, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.ticket import Ticket


def sales_by_period(
    session: Session, *, granularity: str = "day", giveaway_id: int | None = None
) -> list[dict[str, Any]]:
    """Динамика продаж (успешных онлайн-платежей) по дням/месяцам (п.16 ТЗ)."""
    fmt = "%Y-%m-%d" if granularity == "day" else "%Y-%m"
    stmt = select(Payment).where(Payment.status == PaymentStatus.SUCCEEDED)
    if giveaway_id is not None:
        stmt = stmt.where(Payment.giveaway_id == giveaway_id)
    payments = session.execute(stmt).scalars().all()

    buckets: dict[str, dict[str, int]] = {}
    for p in payments:
        key = p.confirmed_at.strftime(fmt) if p.confirmed_at else p.created_at.strftime(fmt)
        bucket = buckets.setdefault(key, {"count": 0, "amount": 0})
        bucket["count"] += 1
        bucket["amount"] += p.amount
    return [{"period": k, **v} for k, v in sorted(buckets.items())]


def online_vs_offline(
    session: Session, *, giveaway_id: int | None = None
) -> dict[str, dict[str, int]]:
    """Сравнение онлайн/офлайн продаж по количеству и выручке (п.16 ТЗ)."""
    online_stmt = select(Payment).where(Payment.status == PaymentStatus.SUCCEEDED)
    if giveaway_id is not None:
        online_stmt = online_stmt.where(Payment.giveaway_id == giveaway_id)
    online_payments = session.execute(online_stmt).scalars().all()

    offline_stmt = (
        select(ManualRegistration, Giveaway.ticket_price)
        .join(Giveaway, Giveaway.id == ManualRegistration.giveaway_id)
        .where(ManualRegistration.status == ManualRegistrationStatus.CONFIRMED)
    )
    if giveaway_id is not None:
        offline_stmt = offline_stmt.where(ManualRegistration.giveaway_id == giveaway_id)
    offline_rows = session.execute(offline_stmt).all()

    return {
        "online": {"count": len(online_payments), "amount": sum(p.amount for p in online_payments)},
        "offline": {
            "count": len(offline_rows),
            "amount": sum(reg.quantity * price for reg, price in offline_rows),
        },
    }


def sales_by_operator(session: Session, *, giveaway_id: int | None = None) -> list[dict[str, Any]]:
    """Активность операторов: количество и объём подтверждённых ручных регистраций (п.16 ТЗ)."""
    stmt = (
        select(
            PanelUser.login,
            func.count(ManualRegistration.id),
            func.coalesce(func.sum(ManualRegistration.quantity), 0),
        )
        .join(ManualRegistration, ManualRegistration.operator_id == PanelUser.id)
        .where(ManualRegistration.status == ManualRegistrationStatus.CONFIRMED)
        .group_by(PanelUser.login)
    )
    if giveaway_id is not None:
        stmt = stmt.where(ManualRegistration.giveaway_id == giveaway_id)
    rows = session.execute(stmt).all()
    return [
        {"operator_login": login, "registrations_count": cnt, "tickets_quantity": qty}
        for login, cnt, qty in rows
    ]


def sales_by_provider(session: Session, *, giveaway_id: int | None = None) -> list[dict[str, Any]]:
    """Разбивка успешных онлайн-платежей по банку (Т-Банк/ВТБ/mock, п.16 ТЗ)."""
    stmt = (
        select(Payment.provider, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.status == PaymentStatus.SUCCEEDED)
        .group_by(Payment.provider)
    )
    if giveaway_id is not None:
        stmt = stmt.where(Payment.giveaway_id == giveaway_id)
    rows = session.execute(stmt).all()
    return [
        {"provider": provider.value, "count": cnt, "amount": amt} for provider, cnt, amt in rows
    ]


def tickets_by_giveaway_and_source(session: Session) -> list[dict[str, Any]]:
    """Выданные номерки по розыгрышам и источнику (online/manual, п.16 ТЗ)."""
    stmt = (
        select(Giveaway.id, Giveaway.name, Ticket.source, func.count(Ticket.id))
        .join(Ticket, Ticket.giveaway_id == Giveaway.id)
        .group_by(Giveaway.id, Giveaway.name, Ticket.source)
    )
    rows = session.execute(stmt).all()
    return [
        {"giveaway_id": gid, "giveaway_name": name, "source": source.value, "count": count}
        for gid, name, source, count in rows
    ]


def participants_report(session: Session) -> list[dict[str, Any]]:
    """Количество номерков на участника (п.16 ТЗ)."""
    stmt = (
        select(Participant.id, Participant.phone, func.count(Ticket.id))
        .join(Ticket, Ticket.participant_id == Participant.id, isouter=True)
        .group_by(Participant.id, Participant.phone)
    )
    rows = session.execute(stmt).all()
    return [
        {"participant_id": pid, "phone": phone, "tickets_count": count}
        for pid, phone, count in rows
    ]


def financial_summary(session: Session, *, giveaway_id: int | None = None) -> dict[str, Any]:
    """Общая выручка (эквайринг + наличные через оператора), число успешных
    платежей, средний чек (п.16 ТЗ).

    `successful_payments_count`/`average_check` намеренно считаются только по
    онлайн-платежам (`Payment`) — ручная регистрация не является дискретным
    «чеком» (сумма = quantity × ticket_price, без отдельной транзакции), поэтому
    у неё нет своего "чека" для усреднения. Не путать это с `revenue_total`,
    который обязан включать оба источника денег (см. DECISIONS.md — баг, когда
    ручная выдача номерков не попадала в выручку).
    """
    revenue = online_vs_offline(session, giveaway_id=giveaway_id)
    online = revenue["online"]
    offline = revenue["offline"]
    average = online["amount"] // online["count"] if online["count"] else 0
    return {
        "revenue_online": online["amount"],
        "revenue_offline": offline["amount"],
        "revenue_total": online["amount"] + offline["amount"],
        "successful_payments_count": online["count"],
        "average_check": average,
    }


def revenue_by_giveaway(session: Session) -> list[dict[str, Any]]:
    """Выручка (эквайринг/наличные/итого) по каждому розыгрышу отдельно (п.16 ТЗ).

    Розыгрыши без активности тоже включаются (с нулями), а не пропускаются.
    Считается двумя раздельными GROUP BY запросами и мёрджится в Python —
    JOIN Payment+ManualRegistration к Giveaway в одном запросе дал бы fan-out
    (декартово произведение платежей на ручные регистрации) и задвоил бы суммы.
    """
    online_stmt = (
        select(
            Payment.giveaway_id,
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount), 0),
        )
        .where(Payment.status == PaymentStatus.SUCCEEDED)
        .group_by(Payment.giveaway_id)
    )
    online_by_giveaway = {gid: amount for gid, _count, amount in session.execute(online_stmt).all()}

    offline_stmt = (
        select(
            ManualRegistration.giveaway_id,
            func.coalesce(func.sum(ManualRegistration.quantity), 0),
        )
        .where(ManualRegistration.status == ManualRegistrationStatus.CONFIRMED)
        .group_by(ManualRegistration.giveaway_id)
    )
    # dict(...) here breaks mypy: SQLAlchemy's Row isn't seen as tuple[int, int].
    offline_quantity_by_giveaway = {  # noqa: C416
        gid: qty for gid, qty in session.execute(offline_stmt).all()
    }

    giveaways = session.execute(
        select(Giveaway.id, Giveaway.name, Giveaway.ticket_price, Giveaway.tickets_issued)
    ).all()

    result = []
    for gid, name, ticket_price, tickets_issued in giveaways:
        revenue_online = online_by_giveaway.get(gid, 0)
        revenue_offline = offline_quantity_by_giveaway.get(gid, 0) * ticket_price
        result.append(
            {
                "giveaway_id": gid,
                "giveaway_name": name,
                "revenue_online": revenue_online,
                "revenue_offline": revenue_offline,
                "revenue_total": revenue_online + revenue_offline,
                "tickets_issued": tickets_issued,
            }
        )
    return result


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    """CSV с `;`-разделителем и UTF-8 BOM — корректно открывается в Excel на
    русской локали (см. DECISIONS.md)."""
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def to_xlsx(rows: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
