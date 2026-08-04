"""Тесты отчётов и экспорта (п.16 ТЗ)."""

from __future__ import annotations

import datetime as dt

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
from app.services import report_service as svc
from sqlalchemy.orm import Session


def make_giveaway(session: Session, prefix: str = "REP") -> Giveaway:
    g = Giveaway(name="Report Test", prefix=prefix, ticket_price=10000, max_tickets=100)
    session.add(g)
    session.flush()
    return g


def make_participant(session: Session, phone: str) -> Participant:
    p = Participant(phone=phone)
    session.add(p)
    session.flush()
    return p


def test_sales_by_provider_and_financial_summary(session: Session) -> None:
    g = make_giveaway(session)
    p = make_participant(session, "79990001111")
    session.add(
        Payment(
            order_id="o1",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=20000,
            quantity=2,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    session.add(
        Payment(
            order_id="o2",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.TBANK,
            amount=10000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    session.add(
        Payment(
            order_id="o3",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=99999,
            quantity=1,
            status=PaymentStatus.FAILED,
        )
    )
    session.flush()

    by_provider = {row["provider"]: row for row in svc.sales_by_provider(session)}
    assert by_provider["mock"]["count"] == 1
    assert by_provider["mock"]["amount"] == 20000
    assert by_provider["tbank"]["count"] == 1
    assert "tbank" not in {} or by_provider["tbank"]["amount"] == 10000

    summary = svc.financial_summary(session)
    assert summary["revenue_online"] == 30000
    assert summary["revenue_offline"] == 0
    assert summary["revenue_offline_cash"] == 0
    assert summary["revenue_offline_cashless"] == 0
    assert summary["revenue_total"] == 30000
    assert summary["successful_payments_count"] == 2
    assert summary["average_check"] == 15000


def test_sales_by_period_date_range_filter(session: Session) -> None:
    g = make_giveaway(session)
    p = make_participant(session, "79990001112")
    session.add(
        Payment(
            order_id="range-old",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=10000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
            confirmed_at=dt.datetime(2026, 1, 1, 12, 0, 0),
        )
    )
    session.add(
        Payment(
            order_id="range-in",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=20000,
            quantity=2,
            status=PaymentStatus.SUCCEEDED,
            confirmed_at=dt.datetime(2026, 3, 15, 12, 0, 0),
        )
    )
    session.add(
        Payment(
            order_id="range-new",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=30000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
            confirmed_at=dt.datetime(2026, 6, 1, 12, 0, 0),
        )
    )
    session.flush()

    rows = svc.sales_by_period(
        session,
        granularity="day",
        date_from=dt.date(2026, 3, 1),
        date_to=dt.date(2026, 3, 31),
    )
    assert rows == [{"period": "2026-03-15", "count": 1, "amount": 20000}]

    # Границы диапазона включительны.
    rows_boundary = svc.sales_by_period(
        session,
        granularity="day",
        date_from=dt.date(2026, 1, 1),
        date_to=dt.date(2026, 1, 1),
    )
    assert rows_boundary == [{"period": "2026-01-01", "count": 1, "amount": 10000}]

    rows_all = svc.sales_by_period(session, granularity="day")
    assert len(rows_all) == 3


def test_sales_by_period_hour_granularity(session: Session) -> None:
    g = make_giveaway(session)
    p = make_participant(session, "79990001113")
    session.add(
        Payment(
            order_id="hour-1",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=10000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
            confirmed_at=dt.datetime(2026, 3, 15, 9, 20, 0),
        )
    )
    session.add(
        Payment(
            order_id="hour-2",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=20000,
            quantity=2,
            status=PaymentStatus.SUCCEEDED,
            confirmed_at=dt.datetime(2026, 3, 15, 9, 45, 0),
        )
    )
    session.add(
        Payment(
            order_id="hour-3",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=5000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
            confirmed_at=dt.datetime(2026, 3, 15, 10, 5, 0),
        )
    )
    session.flush()

    rows = {row["period"]: row for row in svc.sales_by_period(session, granularity="hour")}
    assert rows["2026-03-15T09"] == {"period": "2026-03-15T09", "count": 2, "amount": 30000}
    assert rows["2026-03-15T10"] == {"period": "2026-03-15T10", "count": 1, "amount": 5000}


def test_sales_by_channel(session: Session) -> None:
    g = make_giveaway(session)
    p = make_participant(session, "79990002222")
    session.add(
        Payment(
            order_id="c1",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            channel=ChannelType.TELEGRAM,
            amount=20000,
            quantity=2,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    session.add(
        Payment(
            order_id="c2",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            channel=ChannelType.VK,
            amount=10000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    session.add(
        # Платёж без канала (создан до появления Payment.channel) — попадает в "unknown".
        Payment(
            order_id="c3",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=5000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    session.flush()

    by_channel = {row["channel"]: row for row in svc.sales_by_channel(session)}
    assert by_channel["telegram"]["count"] == 1
    assert by_channel["telegram"]["amount"] == 20000
    assert by_channel["vk"]["count"] == 1
    assert by_channel["vk"]["amount"] == 10000
    assert by_channel["unknown"]["count"] == 1
    assert by_channel["unknown"]["amount"] == 5000


def test_sales_by_operator(session: Session) -> None:
    g = make_giveaway(session)
    p = make_participant(session, "79990002222")
    operator = PanelUser(login="op_report", password_hash="x", role=PanelUserRole.OPERATOR)
    session.add(operator)
    session.flush()
    session.add(
        ManualRegistration(
            participant_id=p.id,
            giveaway_id=g.id,
            quantity=4,
            status=ManualRegistrationStatus.CONFIRMED,
            operator_id=operator.id,
        )
    )
    session.add(
        ManualRegistration(
            participant_id=p.id,
            giveaway_id=g.id,
            quantity=100,
            status=ManualRegistrationStatus.PENDING,
            operator_id=operator.id,
        )
    )
    session.flush()

    rows = svc.sales_by_operator(session)
    assert len(rows) == 1
    assert rows[0]["operator_login"] == "op_report"
    assert rows[0]["registrations_count"] == 1  # только CONFIRMED
    assert rows[0]["tickets_quantity"] == 4


def test_online_vs_offline(session: Session) -> None:
    g = make_giveaway(session)
    p = make_participant(session, "79990003333")
    operator = PanelUser(login="op2", password_hash="x", role=PanelUserRole.OPERATOR)
    session.add(operator)
    session.flush()
    session.add(
        Payment(
            order_id="online1",
            participant_id=p.id,
            giveaway_id=g.id,
            provider=PaymentProviderType.MOCK,
            amount=10000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    session.add(
        ManualRegistration(
            participant_id=p.id,
            giveaway_id=g.id,
            quantity=2,
            status=ManualRegistrationStatus.CONFIRMED,
            operator_id=operator.id,
        )
    )
    session.add(
        ManualRegistration(
            participant_id=p.id,
            giveaway_id=g.id,
            quantity=3,
            status=ManualRegistrationStatus.CONFIRMED,
            operator_id=operator.id,
            payment_method=ManualRegistrationPaymentMethod.CASHLESS,
        )
    )
    session.flush()

    result = svc.online_vs_offline(session)
    assert result["online"] == {"count": 1, "amount": 10000}
    assert result["offline"] == {"count": 2, "amount": 50000}  # (2 + 3) * ticket_price(10000)
    assert result["offline_cash"] == {"count": 1, "amount": 20000}
    assert result["offline_cashless"] == {"count": 1, "amount": 30000}


def test_revenue_by_giveaway(session: Session) -> None:
    g1 = make_giveaway(session, prefix="RBG1")
    g2 = make_giveaway(session, prefix="RBG2")
    g3_empty = make_giveaway(session, prefix="RBG3")
    p = make_participant(session, "79990005555")
    operator = PanelUser(login="op_rbg", password_hash="x", role=PanelUserRole.OPERATOR)
    session.add(operator)
    session.flush()

    session.add(
        Payment(
            order_id="rbg-o1",
            participant_id=p.id,
            giveaway_id=g1.id,
            provider=PaymentProviderType.MOCK,
            amount=5000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    session.add(
        ManualRegistration(
            participant_id=p.id,
            giveaway_id=g1.id,
            quantity=1,
            status=ManualRegistrationStatus.CONFIRMED,
            operator_id=operator.id,
        )
    )
    session.add(
        ManualRegistration(
            participant_id=p.id,
            giveaway_id=g1.id,
            quantity=2,
            status=ManualRegistrationStatus.CONFIRMED,
            operator_id=operator.id,
            payment_method=ManualRegistrationPaymentMethod.CASHLESS,
        )
    )
    session.add(
        Payment(
            order_id="rbg-o2",
            participant_id=p.id,
            giveaway_id=g2.id,
            provider=PaymentProviderType.MOCK,
            amount=7000,
            quantity=1,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    session.flush()

    rows = {row["giveaway_id"]: row for row in svc.revenue_by_giveaway(session)}

    assert rows[g1.id]["revenue_online"] == 5000
    assert rows[g1.id]["revenue_offline"] == 30000  # (1 + 2) * ticket_price(10000)
    assert rows[g1.id]["revenue_offline_cash"] == 10000
    assert rows[g1.id]["revenue_offline_cashless"] == 20000
    assert rows[g1.id]["revenue_total"] == 35000

    assert rows[g2.id]["revenue_online"] == 7000
    assert rows[g2.id]["revenue_offline"] == 0
    assert rows[g2.id]["revenue_total"] == 7000

    assert rows[g3_empty.id]["revenue_online"] == 0
    assert rows[g3_empty.id]["revenue_offline"] == 0
    assert rows[g3_empty.id]["revenue_total"] == 0


def test_participants_report(session: Session) -> None:
    make_participant(session, "79990004444")
    rows = svc.participants_report(session)
    assert any(r["phone"] == "79990004444" and r["tickets_count"] == 0 for r in rows)


def test_export_csv_and_xlsx_smoke() -> None:
    rows = [{"a": 1, "b": "текст"}, {"a": 2, "b": "ещё"}]
    csv_bytes = svc.to_csv(rows)
    assert csv_bytes.startswith("﻿".encode())
    assert "текст".encode() in csv_bytes

    xlsx_bytes = svc.to_xlsx(rows)
    assert xlsx_bytes[:2] == b"PK"  # xlsx — это zip-контейнер


def test_export_csv_and_xlsx_with_list_valued_field() -> None:
    """Регресс: `ParticipantOut.channels`/`ManualRegistrationOut.participant_channels`
    (список каналов участника) падал в openpyxl с `ValueError: Cannot convert [] to
    Excel` — списки должны сериализоваться в строку перед экспортом."""
    rows = [
        {"phone": "79990001111", "channels": ["telegram", "vk"]},
        {"phone": "79990002222", "channels": []},
    ]
    csv_bytes = svc.to_csv(rows)
    assert b"telegram, vk" in csv_bytes

    xlsx_bytes = svc.to_xlsx(rows)
    assert xlsx_bytes[:2] == b"PK"
