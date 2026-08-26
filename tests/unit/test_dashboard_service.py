"""Тесты данных для раздела «Dashboard» (карточки коллекций + операционные
алерты), см. app/services/dashboard_service.py."""

from __future__ import annotations

import datetime as dt
import itertools

from app.core.config import Settings
from app.models.base import utcnow
from app.models.enums import (
    ChannelType,
    ManualRegistrationPaymentMethod,
    ManualRegistrationStatus,
    PanelUserRole,
    PaymentProviderType,
    PaymentStatus,
)
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.services import dashboard_service as svc
from sqlalchemy.orm import Session

_order_id_counter = itertools.count()


def make_giveaway(
    session: Session,
    *,
    prefix: str = "DASH",
    max_tickets: int = 100,
    tickets_issued: int = 0,
    tickets_reserved: int = 0,
    is_registration_open: bool = False,
    is_locked: bool = False,
    is_archived: bool = False,
    opened_at: dt.datetime | None = None,
) -> Giveaway:
    g = Giveaway(
        name=f"Test {prefix}",
        prefix=prefix,
        ticket_price=10000,
        max_tickets=max_tickets,
        tickets_issued=tickets_issued,
        tickets_reserved=tickets_reserved,
        is_registration_open=is_registration_open,
        is_locked=is_locked,
        is_archived=is_archived,
        opened_at=opened_at,
    )
    session.add(g)
    session.flush()
    return g


def make_participant(session: Session, phone: str = "79990009999") -> Participant:
    p = Participant(phone=phone)
    session.add(p)
    session.flush()
    return p


def make_operator(session: Session, login: str = "dash_op") -> PanelUser:
    u = PanelUser(login=login, password_hash="x", role=PanelUserRole.OPERATOR)
    session.add(u)
    session.flush()
    return u


def make_payment(
    session: Session,
    *,
    giveaway_id: int,
    participant_id: int,
    status: PaymentStatus = PaymentStatus.SUCCEEDED,
    amount: int = 10000,
    quantity: int = 1,
    channel: ChannelType | None = None,
    confirmed_at: dt.datetime | None = None,
    amount_mismatch: bool = False,
    amount_mismatch_since: dt.datetime | None = None,
    payment_number: int | None = None,
) -> Payment:
    payment = Payment(
        order_id=f"dash-order-{next(_order_id_counter)}",
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        provider=PaymentProviderType.REQUISITES_QR,
        amount=amount,
        quantity=quantity,
        status=status,
        channel=channel,
        confirmed_at=confirmed_at,
        amount_mismatch=amount_mismatch,
        amount_mismatch_since=amount_mismatch_since,
        payment_number=payment_number,
    )
    session.add(payment)
    session.flush()
    return payment


def make_manual_registration(
    session: Session,
    *,
    giveaway_id: int,
    participant_id: int,
    operator_id: int,
    quantity: int = 1,
    status: ManualRegistrationStatus = ManualRegistrationStatus.CONFIRMED,
    confirmed_at: dt.datetime | None = None,
    created_at: dt.datetime | None = None,
    payment_method: ManualRegistrationPaymentMethod = ManualRegistrationPaymentMethod.CASH,
) -> ManualRegistration:
    reg = ManualRegistration(
        participant_id=participant_id,
        giveaway_id=giveaway_id,
        quantity=quantity,
        status=status,
        operator_id=operator_id,
        confirmed_at=confirmed_at,
        payment_method=payment_method,
    )
    if created_at is not None:
        reg.created_at = created_at
    session.add(reg)
    session.flush()
    return reg


# --- giveaway_cards ---------------------------------------------------------


def test_giveaway_cards_aggregates_revenue_and_ticket_counts(session: Session) -> None:
    g = make_giveaway(
        session,
        prefix="CARD1",
        max_tickets=50,
        tickets_issued=10,
        tickets_reserved=2,
        is_registration_open=True,
        opened_at=utcnow(),
    )
    p = make_participant(session)
    op = make_operator(session)
    make_payment(session, giveaway_id=g.id, participant_id=p.id, amount=5000)
    make_payment(
        session, giveaway_id=g.id, participant_id=p.id, amount=99999, status=PaymentStatus.FAILED
    )
    make_manual_registration(session, giveaway_id=g.id, participant_id=p.id, operator_id=op.id)
    make_manual_registration(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        operator_id=op.id,
        status=ManualRegistrationStatus.PENDING,
        quantity=100,
    )

    cards = {c.id: c for c in svc.giveaway_cards(session)}
    card = cards[g.id]
    assert card.revenue_online == 5000
    assert card.revenue_offline == 10000  # только CONFIRMED (1 * ticket_price)
    assert card.revenue_total == 15000
    assert card.max_tickets == 50
    assert card.tickets_issued == 10
    assert card.tickets_reserved == 2
    assert card.free_tickets_count == 38


def test_giveaway_cards_sparkline_buckets_by_day_within_window(session: Session) -> None:
    g = make_giveaway(session, prefix="SPARK")  # ticket_price=10000
    p = make_participant(session)
    op = make_operator(session)
    now = utcnow()
    two_days_ago = now - dt.timedelta(days=2)
    outside_window = now - dt.timedelta(days=svc.SPARKLINE_DAYS + 5)

    make_payment(session, giveaway_id=g.id, participant_id=p.id, amount=7000, confirmed_at=now)
    make_manual_registration(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        operator_id=op.id,
        quantity=1,
        confirmed_at=two_days_ago,
    )
    make_payment(
        session, giveaway_id=g.id, participant_id=p.id, amount=99999, confirmed_at=outside_window
    )

    card = {c.id: c for c in svc.giveaway_cards(session)}[g.id]
    assert len(card.sparkline) == svc.SPARKLINE_DAYS
    assert card.sparkline[-1] == 7000  # сегодня
    assert card.sparkline[-3] == 10000  # два дня назад: 1 * ticket_price
    # Платёж за пределами окна не должен попасть ни в одну корзину.
    assert sum(card.sparkline) == 7000 + 10000


def test_giveaway_cards_sparkline_all_zero_without_sales(session: Session) -> None:
    g = make_giveaway(session, prefix="SPARKZERO")
    cards = {c.id: c for c in svc.giveaway_cards(session)}
    assert cards[g.id].sparkline == [0] * svc.SPARKLINE_DAYS


def test_giveaway_cards_includes_archived_flagged(session: Session) -> None:
    """Архивные коллекции ТЕПЕРЬ включаются (см. DECISIONS_LOG.md №76) — тумблер
    "только открытые"/"все" фильтрует их на фронте, не на бэкенде; здесь важно
    только то, что карточка приходит и помечена `is_archived=True`."""
    archived = make_giveaway(session, prefix="ARCH", is_archived=True)
    visible = make_giveaway(session, prefix="VIS")

    cards = {c.id: c for c in svc.giveaway_cards(session)}
    assert set(cards) == {archived.id, visible.id}
    assert cards[archived.id].is_archived is True
    assert cards[visible.id].is_archived is False


def test_giveaway_cards_sorted_by_opened_at_desc_nulls_last(session: Session) -> None:
    older = make_giveaway(
        session, prefix="OLD", is_registration_open=True, opened_at=utcnow() - dt.timedelta(days=5)
    )
    newer = make_giveaway(
        session, prefix="NEW", is_registration_open=True, opened_at=utcnow() - dt.timedelta(days=1)
    )
    never_opened = make_giveaway(session, prefix="NEVER", opened_at=None)

    ids = [c.id for c in svc.giveaway_cards(session)]
    assert ids == [newer.id, older.id, never_opened.id]


# --- compute_alerts: low_stock ---------------------------------------------


def test_low_stock_alert_fires_at_or_below_threshold(session: Session, settings: Settings) -> None:
    # 5 свободных из 100 = ровно порог (5%) — должно сработать (граница включительна).
    make_giveaway(
        session,
        prefix="LOW",
        max_tickets=100,
        tickets_issued=95,
        is_registration_open=True,
        opened_at=utcnow(),
    )
    alerts = [a for a in svc.compute_alerts(session, settings) if a.type == "low_stock"]
    assert len(alerts) == 1
    assert alerts[0].free_tickets_count == 5
    assert alerts[0].max_tickets == 100


def test_low_stock_alert_does_not_fire_above_threshold(
    session: Session, settings: Settings
) -> None:
    make_giveaway(
        session,
        prefix="OK",
        max_tickets=100,
        tickets_issued=50,
        is_registration_open=True,
        opened_at=utcnow(),
    )
    alerts = [a for a in svc.compute_alerts(session, settings) if a.type == "low_stock"]
    assert alerts == []


# --- compute_alerts: sales_stalled ------------------------------------------


def test_sales_stalled_alert_fires_when_never_sold_and_opened_long_ago(
    session: Session, settings: Settings
) -> None:
    old_open = utcnow() - dt.timedelta(days=svc.SALES_STALLED_DAYS + 1)
    make_giveaway(
        session, prefix="STALL", max_tickets=100, is_registration_open=True, opened_at=old_open
    )
    alerts = [a for a in svc.compute_alerts(session, settings) if a.type == "sales_stalled"]
    assert len(alerts) == 1
    assert alerts[0].stalled_days == svc.SALES_STALLED_DAYS + 1


def test_sales_stalled_alert_does_not_fire_with_recent_sale(
    session: Session, settings: Settings
) -> None:
    old_open = utcnow() - dt.timedelta(days=svc.SALES_STALLED_DAYS + 1)
    g = make_giveaway(
        session, prefix="RECENT", max_tickets=100, is_registration_open=True, opened_at=old_open
    )
    p = make_participant(session)
    make_payment(session, giveaway_id=g.id, participant_id=p.id, confirmed_at=utcnow())

    alerts = [a for a in svc.compute_alerts(session, settings) if a.type == "sales_stalled"]
    assert alerts == []


def test_sales_stalled_alert_ignores_locked_or_sold_out_giveaway(
    session: Session, settings: Settings
) -> None:
    old_open = utcnow() - dt.timedelta(days=svc.SALES_STALLED_DAYS + 1)
    make_giveaway(
        session,
        prefix="PAUSED",
        max_tickets=100,
        is_registration_open=True,
        is_locked=True,
        opened_at=old_open,
    )
    make_giveaway(
        session,
        prefix="SOLDOUT",
        max_tickets=100,
        tickets_issued=100,
        is_registration_open=True,
        opened_at=old_open,
    )
    alerts = [a for a in svc.compute_alerts(session, settings) if a.type == "sales_stalled"]
    assert alerts == []


# --- compute_alerts: manual_registration_expiring ---------------------------


def test_manual_registration_expiring_alert_fires_past_warn_threshold(
    session: Session, settings: Settings
) -> None:
    g = make_giveaway(session, prefix="MANEXP", is_registration_open=True, opened_at=utcnow())
    p = make_participant(session)
    op = make_operator(session)
    ttl = settings.manual_reservation_ttl_sec
    created_at = utcnow() - dt.timedelta(
        seconds=ttl * svc.MANUAL_REGISTRATION_EXPIRY_WARN_RATIO + 60
    )
    reg = make_manual_registration(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        operator_id=op.id,
        status=ManualRegistrationStatus.PENDING,
        created_at=created_at,
    )

    alerts = [
        a for a in svc.compute_alerts(session, settings) if a.type == "manual_registration_expiring"
    ]
    assert len(alerts) == 1
    assert alerts[0].manual_registration_id == reg.id
    # created_at — на 60 сек ЗА порогом (45 мин из 60-минутного TTL), значит до
    # автоотмены остаётся порядка 15 минут (плюс-минус время выполнения теста).
    assert alerts[0].minutes_until_expiry is not None
    assert 0 < alerts[0].minutes_until_expiry <= 15


def test_manual_registration_expiring_alert_does_not_fire_for_fresh_registration(
    session: Session, settings: Settings
) -> None:
    g = make_giveaway(session, prefix="MANFRESH", is_registration_open=True, opened_at=utcnow())
    p = make_participant(session)
    op = make_operator(session)
    make_manual_registration(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        operator_id=op.id,
        status=ManualRegistrationStatus.PENDING,
        created_at=utcnow(),
    )

    alerts = [
        a for a in svc.compute_alerts(session, settings) if a.type == "manual_registration_expiring"
    ]
    assert alerts == []


# --- compute_alerts: bank_mismatch ------------------------------------------


def test_bank_mismatch_alert_fires_past_threshold(session: Session, settings: Settings) -> None:
    g = make_giveaway(session, prefix="BANKM", is_registration_open=True, opened_at=utcnow())
    p = make_participant(session)
    mismatch_since = utcnow() - dt.timedelta(hours=svc.BANK_MISMATCH_ALERT_HOURS + 1)
    payment = make_payment(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        status=PaymentStatus.PENDING,
        amount_mismatch=True,
        amount_mismatch_since=mismatch_since,
        payment_number=1,
    )

    alerts = [a for a in svc.compute_alerts(session, settings) if a.type == "bank_mismatch"]
    assert len(alerts) == 1
    assert alerts[0].payment_id == payment.id
    assert alerts[0].invoice_no == g.format_invoice_number(1)
    assert alerts[0].hours_open == svc.BANK_MISMATCH_ALERT_HOURS + 1


def test_bank_mismatch_alert_does_not_fire_below_threshold_or_when_resolved(
    session: Session, settings: Settings
) -> None:
    g = make_giveaway(session, prefix="BANKOK", is_registration_open=True, opened_at=utcnow())
    p = make_participant(session)
    # Расхождение только что обнаружено — ещё не пора беспокоиться.
    make_payment(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        status=PaymentStatus.PENDING,
        amount_mismatch=True,
        amount_mismatch_since=utcnow(),
        payment_number=1,
    )
    # Уже закрыт (переплата) — не должен считаться висящим расхождением.
    make_payment(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        status=PaymentStatus.SUCCEEDED,
        amount_mismatch=True,
        amount_mismatch_since=utcnow() - dt.timedelta(hours=svc.BANK_MISMATCH_ALERT_HOURS + 1),
        payment_number=2,
    )

    alerts = [a for a in svc.compute_alerts(session, settings) if a.type == "bank_mismatch"]
    assert alerts == []


# --- средний чек по каналам, % оплаты, скорость продаж, топ, воронка -------


def test_giveaway_cards_average_check_by_channel_and_offline(session: Session) -> None:
    g = make_giveaway(session, prefix="CHECK")  # ticket_price=10000
    p = make_participant(session)
    op = make_operator(session)

    make_payment(
        session, giveaway_id=g.id, participant_id=p.id, amount=15000, channel=ChannelType.TELEGRAM
    )
    make_payment(
        session, giveaway_id=g.id, participant_id=p.id, amount=25000, channel=ChannelType.VK
    )
    # Неуспешный платёж — не должен попасть ни в один средний чек, но должен
    # учитываться в знаменателе online_payments_total.
    make_payment(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        amount=9999,
        status=PaymentStatus.FAILED,
    )
    make_manual_registration(
        session, giveaway_id=g.id, participant_id=p.id, operator_id=op.id, quantity=2
    )

    card = {c.id: c for c in svc.giveaway_cards(session)}[g.id]
    assert card.average_check_telegram == 15000
    assert card.average_check_vk == 25000
    assert card.average_check_offline == 20000  # 2 * ticket_price(10000)
    # total: 3 чека (2 онлайн-успеха + 1 офлайн) на сумму 15000+25000+20000=60000
    assert card.average_check_total == 20000
    assert card.online_payments_total == 3
    assert card.online_payments_succeeded == 2


def test_sales_velocity_last_hour_excludes_older_sales(session: Session) -> None:
    g = make_giveaway(session, prefix="VELOC")  # ticket_price=10000
    p = make_participant(session)
    op = make_operator(session)
    now = utcnow()

    make_payment(
        session, giveaway_id=g.id, participant_id=p.id, amount=5000, quantity=1, confirmed_at=now
    )
    make_manual_registration(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        operator_id=op.id,
        quantity=1,
        confirmed_at=now,
    )
    # За пределами часа — не должен учитываться.
    make_payment(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        amount=99999,
        confirmed_at=now - dt.timedelta(hours=2),
    )

    velocity = svc.sales_velocity_last_hour(session, now=now)
    assert velocity.tickets_count == 2
    assert velocity.revenue == 5000 + 10000


def test_top_participants_by_revenue_combines_sources_and_sorts_desc(session: Session) -> None:
    g = make_giveaway(session, prefix="TOP")  # ticket_price=10000
    op = make_operator(session)
    rich = make_participant(session, phone="79990001111")
    poor = make_participant(session, phone="79990002222")

    make_payment(session, giveaway_id=g.id, participant_id=rich.id, amount=15000)
    make_manual_registration(
        session, giveaway_id=g.id, participant_id=rich.id, operator_id=op.id, quantity=1
    )
    make_payment(session, giveaway_id=g.id, participant_id=poor.id, amount=5000)

    top = svc.top_participants_by_revenue(session, limit=5)
    assert [t.participant_id for t in top] == [rich.id, poor.id]
    assert top[0].revenue_total == 25000
    assert top[0].tickets_count == 2
    assert top[1].revenue_total == 5000


def test_sales_funnel_counts_by_status(session: Session) -> None:
    g = make_giveaway(session, prefix="FUNNEL")
    p = make_participant(session)
    op = make_operator(session)

    make_payment(session, giveaway_id=g.id, participant_id=p.id, status=PaymentStatus.PENDING)
    make_payment(session, giveaway_id=g.id, participant_id=p.id, status=PaymentStatus.SUCCEEDED)
    make_payment(session, giveaway_id=g.id, participant_id=p.id, status=PaymentStatus.SUCCEEDED)
    make_payment(session, giveaway_id=g.id, participant_id=p.id, status=PaymentStatus.FAILED)
    make_payment(session, giveaway_id=g.id, participant_id=p.id, status=PaymentStatus.CANCELLED)

    make_manual_registration(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        operator_id=op.id,
        status=ManualRegistrationStatus.CONFIRMED,
    )
    make_manual_registration(
        session,
        giveaway_id=g.id,
        participant_id=p.id,
        operator_id=op.id,
        status=ManualRegistrationStatus.CANCELLED,
    )

    online, manual = svc.sales_funnel(session)
    assert online.pending == 1
    assert online.succeeded == 2
    assert online.failed == 1
    assert online.cancelled == 1
    assert online.refunded == 0

    assert manual.confirmed == 1
    assert manual.cancelled == 1
    assert manual.pending == 0
    assert manual.refunded == 0
