"""Отчёты (п.16 ТЗ): продажи по периодам, онлайн/офлайн, по операторам, по
провайдерам, номерки по розыгрышам/источнику, по участникам, финансовая сводка.
Экспорт — CSV (`;`, UTF-8 BOM) и XLSX (openpyxl).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import (
    ManualRegistrationPaymentMethod,
    ManualRegistrationStatus,
    PaymentStatus,
)
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.ticket import Ticket

_PERIOD_FORMATS = {"hour": "%Y-%m-%dT%H", "day": "%Y-%m-%d", "month": "%Y-%m"}


def sales_by_period(
    session: Session,
    *,
    granularity: str = "day",
    giveaway_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Динамика продаж (успешных онлайн-платежей) по часам/дням/месяцам (п.16 ТЗ).

    `date_from`/`date_to` — включительно, фильтруют по той же дате (confirmed_at,
    иначе created_at), что используется для группировки, чтобы график и диапазон
    не расходились."""
    fmt = _PERIOD_FORMATS.get(granularity, _PERIOD_FORMATS["day"])
    stmt = select(Payment).where(Payment.status == PaymentStatus.SUCCEEDED)
    if giveaway_id is not None:
        stmt = stmt.where(Payment.giveaway_id == giveaway_id)
    payments = session.execute(stmt).scalars().all()

    buckets: dict[str, dict[str, int]] = {}
    for p in payments:
        dt = p.confirmed_at or p.created_at
        if date_from is not None and dt.date() < date_from:
            continue
        if date_to is not None and dt.date() > date_to:
            continue
        key = dt.strftime(fmt)
        bucket = buckets.setdefault(key, {"count": 0, "amount": 0})
        bucket["count"] += 1
        bucket["amount"] += p.amount
    return [{"period": k, **v} for k, v in sorted(buckets.items())]


def online_vs_offline(
    session: Session, *, giveaway_id: int | None = None
) -> dict[str, dict[str, int]]:
    """Сравнение онлайн/офлайн продаж по количеству и выручке (п.16 ТЗ).

    `offline` разбивается на `offline_cash` (наличные оператору в кассу) и
    `offline_cashless` (безнал по QR, сформированному оператором по просьбе
    покупателя, см. DECISIONS.md) — кассе важно видеть только `offline_cash` как
    реально полученные наличные, `offline_cashless` для неё — не деньги в ящике.
    `offline` остаётся суммой обоих, для обратной совместимости с потребителями,
    которым нужен только общий офлайн-итог (напр. `dashboard.py`)."""
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
    cash_rows = [
        (reg, price)
        for reg, price in offline_rows
        if reg.payment_method == ManualRegistrationPaymentMethod.CASH
    ]
    cashless_rows = [
        (reg, price)
        for reg, price in offline_rows
        if reg.payment_method == ManualRegistrationPaymentMethod.CASHLESS
    ]

    def _bucket(rows: Sequence[Any]) -> dict[str, int]:
        return {"count": len(rows), "amount": sum(reg.quantity * price for reg, price in rows)}

    return {
        "online": {"count": len(online_payments), "amount": sum(p.amount for p in online_payments)},
        "offline": _bucket(offline_rows),
        "offline_cash": _bucket(cash_rows),
        "offline_cashless": _bucket(cashless_rows),
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


def sales_by_channel(session: Session, *, giveaway_id: int | None = None) -> list[dict[str, Any]]:
    """Разбивка успешных онлайн-платежей по мессенджер-каналу (Telegram/VK, п.16 ТЗ).

    `Payment.channel` появилось позже платежей, созданных раньше — у них NULL,
    отображаем как `"unknown"` (задним числом не восстанавливаем, см. DECISIONS.md)."""
    stmt = (
        select(Payment.channel, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.status == PaymentStatus.SUCCEEDED)
        .group_by(Payment.channel)
    )
    if giveaway_id is not None:
        stmt = stmt.where(Payment.giveaway_id == giveaway_id)
    rows = session.execute(stmt).all()
    return [
        {"channel": channel.value if channel else "unknown", "count": cnt, "amount": amt}
        for channel, cnt, amt in rows
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
    """Общая выручка (эквайринг + офлайн через оператора — наличные и безнал по
    QR), число успешных платежей, средний чек (п.16 ТЗ). `revenue_offline_cash`/
    `revenue_offline_cashless` — разбивка офлайн-выручки для кассы (см.
    `online_vs_offline`, DECISIONS.md): реальные наличные в ящике — только
    `revenue_offline_cash`.

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
        "revenue_offline_cash": revenue["offline_cash"]["amount"],
        "revenue_offline_cashless": revenue["offline_cashless"]["amount"],
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

    # Группировка ещё и по payment_method — для разбивки кассовой (CASH) и
    # безналичной (CASHLESS) офлайн-выручки по розыгрышу, см. `online_vs_offline`.
    offline_stmt = (
        select(
            ManualRegistration.giveaway_id,
            ManualRegistration.payment_method,
            func.coalesce(func.sum(ManualRegistration.quantity), 0),
        )
        .where(ManualRegistration.status == ManualRegistrationStatus.CONFIRMED)
        .group_by(ManualRegistration.giveaway_id, ManualRegistration.payment_method)
    )
    offline_cash_quantity_by_giveaway: dict[int, int] = {}
    offline_cashless_quantity_by_giveaway: dict[int, int] = {}
    for gid, method, qty in session.execute(offline_stmt).all():
        bucket = (
            offline_cash_quantity_by_giveaway
            if method == ManualRegistrationPaymentMethod.CASH
            else offline_cashless_quantity_by_giveaway
        )
        bucket[gid] = qty

    giveaways = session.execute(
        select(Giveaway.id, Giveaway.name, Giveaway.ticket_price, Giveaway.tickets_issued)
    ).all()

    result = []
    for gid, name, ticket_price, tickets_issued in giveaways:
        revenue_online = online_by_giveaway.get(gid, 0)
        revenue_offline_cash = offline_cash_quantity_by_giveaway.get(gid, 0) * ticket_price
        revenue_offline_cashless = offline_cashless_quantity_by_giveaway.get(gid, 0) * ticket_price
        revenue_offline = revenue_offline_cash + revenue_offline_cashless
        result.append(
            {
                "giveaway_id": gid,
                "giveaway_name": name,
                "revenue_online": revenue_online,
                "revenue_offline": revenue_offline,
                "revenue_offline_cash": revenue_offline_cash,
                "revenue_offline_cashless": revenue_offline_cashless,
                "revenue_total": revenue_online + revenue_offline,
                "tickets_issued": tickets_issued,
            }
        )
    return result


def _stringify_lists(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CSV/XLSX не умеют писать список в одну ячейку (openpyxl падает с
    `ValueError: Cannot convert [] to Excel`) — сериализуем list-значения
    (например, `ParticipantOut.channels`) в строку через запятую перед экспортом."""
    return [
        {k: (", ".join(v) if isinstance(v, list) else v) for k, v in row.items()} for row in rows
    ]


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    """CSV с `;`-разделителем и UTF-8 BOM — корректно открывается в Excel на
    русской локали (см. DECISIONS.md)."""
    rows = _stringify_lists(rows)
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def to_xlsx(rows: list[dict[str, Any]]) -> bytes:
    rows = _stringify_lists(rows)
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
