"""Тесты сверки платежей requisites_qr по выписке расчётного счёта
(app/services/bank_reconciliation_service.py, см. DECISIONS.md, ARCHITECTURE.md §7.2)."""

from __future__ import annotations

import datetime as dt

from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import PaymentStatus
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.payment import Payment
from app.payments.bank_statement import BankStatementEntry
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import bank_reconciliation_service as svc
from app.services import payment_service as payment_svc
from app.services import ticket_pool_service as pool_svc
from sqlalchemy import select


def make_giveaway(db: Database, *, max_tickets: int = 10, prefix: str = "REQ") -> int:
    with db.session() as session:
        g = Giveaway(name="Test", prefix=prefix, ticket_price=10000, max_tickets=max_tickets)
        session.add(g)
        session.flush()
        pool_svc.open_registration(session, g)
        return g.id


def make_participant(db: Database, phone: str = "79991234567") -> int:
    with db.session() as session:
        p = Participant(phone=phone)
        session.add(p)
        session.flush()
        return p.id


def make_pending_payment(db: Database, *, giveaway_id: int, participant_id: int, quantity: int = 1):
    provider = RequisitesQrProvider(
        recipient_name="ООО Тест",
        recipient_inn="7700000000",
        recipient_kpp="",
        personal_acc="40702810900000000000",
        bank_name="Т-Банк",
        bic="044525974",
        corresp_acc="30101810145250000974",
        vat_rate_percent=20,
    )
    return payment_svc.create_payment_safe(
        db,
        provider,
        giveaway_id=giveaway_id,
        participant_id=participant_id,
        participant_phone="79991234567",
        quantity=quantity,
    )


def test_find_matching_entry_matches_by_prefix_number_and_amount() -> None:
    entries = [
        BankStatementEntry(
            external_id="op-1",
            amount=16670,
            purpose="Оплата по счету № REQ-00001 от 22.07.2026, в т.ч. НДС 20% 16.67 руб.",
            operation_date=utcnow(),
        )
    ]
    result = svc.find_matching_entry(entries, "REQ-00001", 16670)
    assert result.matched is not None
    assert result.matched.external_id == "op-1"
    assert result.mismatched is None


def test_find_matching_entry_no_match_for_different_invoice() -> None:
    entries = [
        BankStatementEntry(
            external_id="op-1",
            amount=10000,
            purpose="Оплата по счету № OTHER-00099 от 22.07.2026",
            operation_date=utcnow(),
        )
    ]
    result = svc.find_matching_entry(entries, "REQ-00001", 10000)
    assert result.matched is None
    assert result.mismatched is None


def test_find_matching_entry_reports_mismatch_when_amount_differs() -> None:
    entries = [
        BankStatementEntry(
            external_id="op-1",
            amount=9999,  # участник заплатил меньше/больше ожидаемого
            purpose="Оплата по счету № REQ-00001 от 22.07.2026",
            operation_date=utcnow(),
        )
    ]
    result = svc.find_matching_entry(entries, "REQ-00001", 10000)
    assert result.matched is None
    assert result.mismatched is not None
    assert result.mismatched.external_id == "op-1"
    assert result.mismatched.amount == 9999


class _FakeStatementProvider:
    def __init__(self, entries: list[BankStatementEntry]) -> None:
        self._entries = entries

    def fetch_operations(self, *, since: dt.datetime) -> list[BankStatementEntry]:
        return self._entries

    @classmethod
    def from_settings(cls, settings: Settings) -> _FakeStatementProvider:
        return cls(getattr(settings, "_fake_entries", []))


def test_reconcile_finalizes_matched_pending_payment(
    db: Database, settings: Settings, monkeypatch
) -> None:
    gid = make_giveaway(db)
    pid = make_participant(db)
    outcome = make_pending_payment(db, giveaway_id=gid, participant_id=pid, quantity=2)
    assert outcome.ok
    assert outcome.invoice_no is not None

    entries = [
        BankStatementEntry(
            external_id="op-1",
            amount=outcome.amount or 0,
            purpose=f"Оплата по счету № {outcome.invoice_no} от 22.07.2026",
            operation_date=utcnow(),
        )
    ]
    settings._fake_entries = entries  # type: ignore[attr-defined]
    monkeypatch.setattr(svc, "TBankStatementProvider", _FakeStatementProvider)

    outcomes = svc.reconcile(db, settings)

    assert len(outcomes) == 1
    assert outcomes[0].applied
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.SUCCEEDED


def test_reconcile_does_not_touch_unmatched_payment(
    db: Database, settings: Settings, monkeypatch
) -> None:
    gid = make_giveaway(db)
    pid = make_participant(db)
    outcome = make_pending_payment(db, giveaway_id=gid, participant_id=pid, quantity=1)
    assert outcome.ok

    settings._fake_entries = []  # type: ignore[attr-defined]
    monkeypatch.setattr(svc, "TBankStatementProvider", _FakeStatementProvider)

    outcomes = svc.reconcile(db, settings)

    assert outcomes == []
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.PENDING


def test_reconcile_flags_amount_mismatch_instead_of_finalizing(
    db: Database, settings: Settings, monkeypatch
) -> None:
    gid = make_giveaway(db)
    pid = make_participant(db)
    outcome = make_pending_payment(db, giveaway_id=gid, participant_id=pid, quantity=2)
    assert outcome.ok
    assert outcome.invoice_no is not None
    wrong_amount = (outcome.amount or 0) - 1  # заплатили меньше, чем в заказе

    entries = [
        BankStatementEntry(
            external_id="op-1",
            amount=wrong_amount,
            purpose=f"Оплата по счету № {outcome.invoice_no} от 22.07.2026",
            operation_date=utcnow(),
        )
    ]
    settings._fake_entries = entries  # type: ignore[attr-defined]
    monkeypatch.setattr(svc, "TBankStatementProvider", _FakeStatementProvider)

    outcomes = svc.reconcile(db, settings)

    assert outcomes == []
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.PENDING
        assert payment.amount_mismatch is True
        assert payment.amount_mismatch_bank_amount == wrong_amount


def test_reconcile_does_not_ttl_expire_payment_with_active_amount_mismatch(
    db: Database, settings: Settings, monkeypatch
) -> None:
    gid = make_giveaway(db)
    pid = make_participant(db)
    outcome = make_pending_payment(db, giveaway_id=gid, participant_id=pid, quantity=1)
    assert outcome.ok
    assert outcome.invoice_no is not None

    entries = [
        BankStatementEntry(
            external_id="op-1",
            amount=(outcome.amount or 0) - 1,
            purpose=f"Оплата по счету № {outcome.invoice_no} от 22.07.2026",
            operation_date=utcnow(),
        )
    ]
    settings._fake_entries = entries  # type: ignore[attr-defined]
    monkeypatch.setattr(svc, "TBankStatementProvider", _FakeStatementProvider)

    far_future = utcnow() + dt.timedelta(days=settings.requisites_invoice_ttl_days + 1)
    outcomes = svc.reconcile(db, settings, now=far_future)

    assert outcomes == []
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        # Не FAILED, несмотря на просроченный TTL — деньги фактически идут,
        # нужен ручной разбор расхождения, а не автозакрытие счёта.
        assert payment.status == PaymentStatus.PENDING
        assert payment.amount_mismatch is True


def test_reconcile_expires_stale_unmatched_invoice_past_ttl(
    db: Database, settings: Settings, monkeypatch
) -> None:
    gid = make_giveaway(db)
    pid = make_participant(db)
    outcome = make_pending_payment(db, giveaway_id=gid, participant_id=pid, quantity=1)
    assert outcome.ok

    settings._fake_entries = []  # type: ignore[attr-defined]
    monkeypatch.setattr(svc, "TBankStatementProvider", _FakeStatementProvider)

    far_future = utcnow() + dt.timedelta(days=settings.requisites_invoice_ttl_days + 1)
    outcomes = svc.reconcile(db, settings, now=far_future)

    assert len(outcomes) == 1
    assert outcomes[0].applied
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.FAILED
