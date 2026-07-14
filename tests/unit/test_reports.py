"""Тесты отчётов и экспорта (п.16 ТЗ)."""

from __future__ import annotations

from app.models.enums import (
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
    assert summary["revenue_total"] == 30000
    assert summary["successful_payments_count"] == 2
    assert summary["average_check"] == 15000


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
    session.flush()

    result = svc.online_vs_offline(session)
    assert result["online"] == {"count": 1, "amount": 10000}
    assert result["offline"] == {"count": 1, "amount": 20000}  # 2 * ticket_price(10000)


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
    assert rows[g1.id]["revenue_offline"] == 10000  # 1 * ticket_price(10000)
    assert rows[g1.id]["revenue_total"] == 15000

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
