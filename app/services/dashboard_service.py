"""Данные для раздела «Dashboard» сверх плоских агрегатов за всё время (те
считает `backend/api/dashboard.py` напрямую): разбивка по коллекциям и
операционные алерты, требующие внимания прямо сейчас.

Пороги алертов ниже — сознательно выбранные константы, а не поля `Settings`:
это не эксплуатационный параметр вроде TTL резервирования, который меняют по
среде, а решение продукта о чувствительности дашборда (согласовано с владельцем
при внедрении раздела). Порог `manual_reservation_ttl_sec` для истечения ручной
регистрации — исключение, он берётся из `Settings` умышленно (см. комментарий у
`MANUAL_REGISTRATION_EXPIRY_WARN_RATIO`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.base import utcnow
from app.models.enums import ManualRegistrationStatus, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.payment import Payment

# "Тираж почти распродан" — доля свободных номерков от общего лимита, начиная с
# которой коллекция попадает в алерт (0 тоже подпадает — полностью распродано).
LOW_STOCK_FREE_RATIO = 0.05

# "Простой продаж" — сколько дней подряд у ОТКРЫТОЙ ДЛЯ ПРОДАЖ прямо сейчас
# коллекции (is_open_for_sale — открыта, не приостановлена, есть свободные
# номерки) нет ни одной продажи (ни одного успешного онлайн-платежа, ни одной
# подтверждённой ручной регистрации), прежде чем считать это поводом для
# внимания. Отсчитывается от последней продажи, а если продаж не было ни разу —
# от момента открытия регистрации (opened_at).
SALES_STALLED_DAYS = 3

# "Расхождение банковской сверки висит" — сколько часов Payment остаётся PENDING
# с amount_mismatch=True (недоплата по счёту requisites_qr), прежде чем считать
# это требующим ручного разбора оператором, а не просто "участник ещё довносит
# остаток отдельным переводом" (см. app/services/bank_reconciliation_service.py).
BANK_MISMATCH_ALERT_HOURS = 3

# "Ручная регистрация скоро истечёт" — доля от manual_reservation_ttl_sec, после
# которой PENDING-регистрация попадает в алерт. Доля, а не абсолютные минуты —
# срок жизни резерва настраивается через Settings по среде, и порог должен
# двигаться вместе с ним: иначе после увеличения TTL алерт будет срабатывать
# слишком рано (или наоборот, не успевать) относительно реального момента
# автоотмены (`backend/background/_release_expired_manual_registrations`).
MANUAL_REGISTRATION_EXPIRY_WARN_RATIO = 0.75

# Длина спарклайна на карточке коллекции (выручка по дням, оба источника) —
# 14 дней достаточно, чтобы увидеть форму тренда, не растягивая узкую карточку.
SPARKLINE_DAYS = 14


@dataclass(frozen=True)
class GiveawayCard:
    id: int
    name: str
    prefix: str
    is_registration_open: bool
    is_locked: bool
    is_closed_forever: bool
    is_archived: bool
    opened_at: dt.datetime | None
    max_tickets: int
    tickets_issued: int
    tickets_reserved: int
    free_tickets_count: int
    revenue_online: int
    revenue_offline: int
    revenue_total: int
    # Выручка (онлайн + офлайн) по последним SPARKLINE_DAYS дням, от старого к
    # новому, включая сегодняшний неполный день — см. _daily_revenue_by_giveaway.
    sparkline: list[int]


def _daily_revenue_by_giveaway(session: Session, giveaway_ids: list[int]) -> dict[int, list[int]]:
    """Выручка по дням за последние SPARKLINE_DAYS на каждую коллекцию — для
    спарклайна на карточке (см. GiveawayCard.sparkline). В отличие от
    `report_service.sales_by_period` (одноразовый просмотр на «Отчётах») здесь
    важна лёгкость запроса: Dashboard опрашивается раз в 3 секунды, поэтому
    период отсекается на уровне SQL, а не после загрузки всех платежей/регистраций
    коллекции в Python."""
    if not giveaway_ids:
        return {}

    now = utcnow()
    cutoff_date = (now - dt.timedelta(days=SPARKLINE_DAYS - 1)).date()
    day_keys = [(cutoff_date + dt.timedelta(days=i)).isoformat() for i in range(SPARKLINE_DAYS)]
    cutoff = dt.datetime.combine(cutoff_date, dt.time.min, tzinfo=now.tzinfo)

    online_by_giveaway: dict[int, dict[str, int]] = {}
    online_stmt = select(
        Payment.giveaway_id, Payment.confirmed_at, Payment.created_at, Payment.amount
    ).where(
        Payment.status == PaymentStatus.SUCCEEDED,
        Payment.giveaway_id.in_(giveaway_ids),
        or_(Payment.confirmed_at >= cutoff, Payment.created_at >= cutoff),
    )
    for gid, confirmed_at, created_at, amount in session.execute(online_stmt).all():
        key = (confirmed_at or created_at).date().isoformat()
        bucket = online_by_giveaway.setdefault(gid, {})
        bucket[key] = bucket.get(key, 0) + amount

    offline_by_giveaway: dict[int, dict[str, int]] = {}
    offline_stmt = (
        select(
            ManualRegistration.giveaway_id,
            ManualRegistration.confirmed_at,
            ManualRegistration.quantity,
            Giveaway.ticket_price,
        )
        .join(Giveaway, Giveaway.id == ManualRegistration.giveaway_id)
        .where(
            ManualRegistration.status == ManualRegistrationStatus.CONFIRMED,
            ManualRegistration.giveaway_id.in_(giveaway_ids),
            ManualRegistration.confirmed_at >= cutoff,
        )
    )
    for gid, confirmed_at, quantity, ticket_price in session.execute(offline_stmt).all():
        key = confirmed_at.date().isoformat()
        bucket = offline_by_giveaway.setdefault(gid, {})
        bucket[key] = bucket.get(key, 0) + quantity * ticket_price

    result: dict[int, list[int]] = {}
    for gid in giveaway_ids:
        online_days = online_by_giveaway.get(gid, {})
        offline_days = offline_by_giveaway.get(gid, {})
        result[gid] = [online_days.get(k, 0) + offline_days.get(k, 0) for k in day_keys]
    return result


def giveaway_cards(session: Session) -> list[GiveawayCard]:
    """Карточка на каждую коллекцию, включая заархивированные — тумблер "только
    открытые" / "все" на фронте (`DashboardPage.tsx`) сам решает, показывать ли
    архивные в режиме "все" (см. DECISIONS_LOG.md №75/№76); бэкенд отдаёт всё
    разом, не дёргая лишний запрос на каждое переключение.

    Сортировка — по давности открытия регистрации (новые сверху), ещё не
    открытые (`opened_at IS NULL`) — в конце, по дате создания."""
    giveaways = list(
        session.execute(
            select(Giveaway).order_by(
                Giveaway.opened_at.is_(None), Giveaway.opened_at.desc(), Giveaway.created_at.desc()
            )
        )
        .scalars()
        .all()
    )
    if not giveaways:
        return []
    giveaway_ids = [g.id for g in giveaways]

    online_stmt = (
        select(Payment.giveaway_id, func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.status == PaymentStatus.SUCCEEDED, Payment.giveaway_id.in_(giveaway_ids))
        .group_by(Payment.giveaway_id)
    )
    online_by_giveaway = dict(session.execute(online_stmt).tuples().all())

    # Как и в report_service.revenue_by_giveaway — офлайн выручка считается из
    # количества (quantity * ticket_price), отдельным GROUP BY, а не JOIN'ом с
    # Payment в одном запросе (дал бы fan-out и задвоил суммы).
    offline_stmt = (
        select(
            ManualRegistration.giveaway_id, func.coalesce(func.sum(ManualRegistration.quantity), 0)
        )
        .where(
            ManualRegistration.status == ManualRegistrationStatus.CONFIRMED,
            ManualRegistration.giveaway_id.in_(giveaway_ids),
        )
        .group_by(ManualRegistration.giveaway_id)
    )
    offline_qty_by_giveaway = dict(session.execute(offline_stmt).tuples().all())
    sparkline_by_giveaway = _daily_revenue_by_giveaway(session, giveaway_ids)

    cards = []
    for g in giveaways:
        revenue_online = online_by_giveaway.get(g.id, 0)
        revenue_offline = offline_qty_by_giveaway.get(g.id, 0) * g.ticket_price
        cards.append(
            GiveawayCard(
                id=g.id,
                name=g.name,
                prefix=g.prefix,
                is_registration_open=g.is_registration_open,
                is_locked=g.is_locked,
                is_closed_forever=g.is_closed_forever,
                is_archived=g.is_archived,
                opened_at=g.opened_at,
                max_tickets=g.max_tickets,
                tickets_issued=g.tickets_issued,
                tickets_reserved=g.tickets_reserved,
                free_tickets_count=g.free_tickets_count,
                revenue_online=revenue_online,
                revenue_offline=revenue_offline,
                revenue_total=revenue_online + revenue_offline,
                sparkline=sparkline_by_giveaway.get(g.id, [0] * SPARKLINE_DAYS),
            )
        )
    return cards


AlertType = Literal["low_stock", "sales_stalled", "manual_registration_expiring", "bank_mismatch"]


@dataclass(frozen=True)
class DashboardAlert:
    """Плоская структура на все 4 типа алертов (как `MonitorRow` на фронте
    нормализует online/manual в одну строку) — поля, не относящиеся к
    конкретному `type`, остаются `None`. Русский текст алерта собирает фронт
    (см. `CHANNEL_LABELS`-подобные словари в других страницах), сюда сложены
    только сырые данные для этого."""

    type: AlertType
    giveaway_id: int | None = None
    giveaway_name: str | None = None
    # low_stock
    free_tickets_count: int | None = None
    max_tickets: int | None = None
    # sales_stalled
    stalled_days: int | None = None
    # manual_registration_expiring
    manual_registration_id: int | None = None
    minutes_until_expiry: int | None = None
    # bank_mismatch
    payment_id: int | None = None
    invoice_no: str | None = None
    hours_open: int | None = None


def _low_stock_alerts(open_giveaways: list[Giveaway]) -> list[DashboardAlert]:
    alerts = []
    for g in open_giveaways:
        if g.max_tickets <= 0:
            continue
        if g.free_tickets_count / g.max_tickets <= LOW_STOCK_FREE_RATIO:
            alerts.append(
                DashboardAlert(
                    type="low_stock",
                    giveaway_id=g.id,
                    giveaway_name=g.name,
                    free_tickets_count=g.free_tickets_count,
                    max_tickets=g.max_tickets,
                )
            )
    return alerts


def _sales_stalled_alerts(
    session: Session, open_giveaways: list[Giveaway], *, now: dt.datetime
) -> list[DashboardAlert]:
    # Только реально продающиеся прямо сейчас (не приостановленные, есть
    # свободные номерки) — у приостановленной или распроданной коллекции
    # отсутствие новых продаж ожидаемо и не требует внимания отдельно от
    # low_stock/самого факта паузы.
    sellable = [g for g in open_giveaways if g.is_open_for_sale and g.opened_at is not None]
    if not sellable:
        return []
    ids = [g.id for g in sellable]

    last_online_stmt = (
        select(Payment.giveaway_id, func.max(Payment.confirmed_at))
        .where(Payment.status == PaymentStatus.SUCCEEDED, Payment.giveaway_id.in_(ids))
        .group_by(Payment.giveaway_id)
    )
    last_online = dict(session.execute(last_online_stmt).tuples().all())
    last_manual_stmt = (
        select(ManualRegistration.giveaway_id, func.max(ManualRegistration.confirmed_at))
        .where(
            ManualRegistration.status == ManualRegistrationStatus.CONFIRMED,
            ManualRegistration.giveaway_id.in_(ids),
        )
        .group_by(ManualRegistration.giveaway_id)
    )
    last_manual = dict(session.execute(last_manual_stmt).tuples().all())

    stalled_cutoff = now - dt.timedelta(days=SALES_STALLED_DAYS)
    alerts = []
    for g in sellable:
        last_sale = last_online.get(g.id)
        manual_sale = last_manual.get(g.id)
        if manual_sale is not None and (last_sale is None or manual_sale > last_sale):
            last_sale = manual_sale
        anchor = last_sale or g.opened_at
        if anchor is not None and anchor < stalled_cutoff:
            alerts.append(
                DashboardAlert(
                    type="sales_stalled",
                    giveaway_id=g.id,
                    giveaway_name=g.name,
                    stalled_days=(now - anchor).days,
                )
            )
    return alerts


def _manual_registration_expiring_alerts(
    session: Session, settings: Settings, *, now: dt.datetime
) -> list[DashboardAlert]:
    ttl = dt.timedelta(seconds=settings.manual_reservation_ttl_sec)
    pending_cutoff = now - ttl * MANUAL_REGISTRATION_EXPIRY_WARN_RATIO
    rows = session.execute(
        select(ManualRegistration, Giveaway.name)
        .join(Giveaway, Giveaway.id == ManualRegistration.giveaway_id)
        .where(
            ManualRegistration.status == ManualRegistrationStatus.PENDING,
            ManualRegistration.created_at <= pending_cutoff,
        )
    ).all()
    alerts = []
    for reg, giveaway_name in rows:
        expires_at = reg.created_at + ttl
        minutes_left = max(0, int((expires_at - now).total_seconds() // 60))
        alerts.append(
            DashboardAlert(
                type="manual_registration_expiring",
                giveaway_id=reg.giveaway_id,
                giveaway_name=giveaway_name,
                manual_registration_id=reg.id,
                minutes_until_expiry=minutes_left,
            )
        )
    return alerts


def _bank_mismatch_alerts(session: Session, *, now: dt.datetime) -> list[DashboardAlert]:
    mismatch_cutoff = now - dt.timedelta(hours=BANK_MISMATCH_ALERT_HOURS)
    rows = session.execute(
        select(Payment, Giveaway)
        .join(Giveaway, Giveaway.id == Payment.giveaway_id)
        .where(
            Payment.status == PaymentStatus.PENDING,
            Payment.amount_mismatch.is_(True),
            Payment.amount_mismatch_since.is_not(None),
            Payment.amount_mismatch_since <= mismatch_cutoff,
        )
    ).all()
    alerts = []
    for payment, giveaway in rows:
        assert payment.amount_mismatch_since is not None  # filtered above
        invoice_no = (
            giveaway.format_invoice_number(payment.payment_number)
            if payment.payment_number is not None
            else None
        )
        alerts.append(
            DashboardAlert(
                type="bank_mismatch",
                giveaway_id=giveaway.id,
                giveaway_name=giveaway.name,
                payment_id=payment.id,
                invoice_no=invoice_no,
                hours_open=int((now - payment.amount_mismatch_since).total_seconds() // 3600),
            )
        )
    return alerts


def compute_alerts(
    session: Session, settings: Settings, *, now: dt.datetime | None = None
) -> list[DashboardAlert]:
    """Все операционные алерты для Dashboard, в фиксированном порядке
    (тираж/простой/регистрации/сверка) — сортировку внутри каждого типа
    (по срочности) при необходимости делает фронт."""
    now = now or utcnow()
    open_giveaways = list(
        session.execute(
            select(Giveaway).where(
                Giveaway.is_archived.is_(False), Giveaway.is_registration_open.is_(True)
            )
        )
        .scalars()
        .all()
    )
    return [
        *_low_stock_alerts(open_giveaways),
        *_sales_stalled_alerts(session, open_giveaways, now=now),
        *_manual_registration_expiring_alerts(session, settings, now=now),
        *_bank_mismatch_alerts(session, now=now),
    ]
