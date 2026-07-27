"""Тесты сверки платежей requisites_qr по выписке расчётного счёта
(app/services/bank_reconciliation_service.py, см. DECISIONS.md, ARCHITECTURE.md §7.2).

Отдельный блок тестов внизу файла покрывает `BankReconciliationRun`/`get_reconciliation_status`
(панель статуса «Сверка выписок» в «Продажи», см. DECISIONS_LOG.md №48)."""

from __future__ import annotations

import datetime as dt
import itertools

from app.core.config import Settings
from app.core.db import Database
from app.models.bank_reconciliation_run import BankReconciliationRun
from app.models.base import utcnow
from app.models.enums import BankReconciliationRunStatus, PaymentProviderType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.payment import Payment
from app.payments.bank_statement import BankStatementEntry
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import bank_reconciliation_service as svc
from app.services import payment_service as payment_svc
from app.services import ticket_pool_service as pool_svc
from sqlalchemy import select

_order_id_counter = itertools.count()


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


def make_raw_payment(
    db: Database,
    *,
    giveaway_id: int,
    participant_id: int,
    created_at: dt.datetime,
    status: PaymentStatus = PaymentStatus.PENDING,
    provider: PaymentProviderType = PaymentProviderType.REQUISITES_QR,
    amount: int = 10000,
    amount_mismatch: bool = False,
) -> int:
    """Вставляет `Payment` напрямую, минуя `payment_service`, — нужен полный
    контроль над `created_at`/`status` для тестов агрегации по дню создания
    (`get_payments_brief`), которых через обычный флоу не добиться."""
    with db.session() as session:
        payment = Payment(
            order_id=f"test-order-{next(_order_id_counter)}",
            participant_id=participant_id,
            giveaway_id=giveaway_id,
            provider=provider,
            amount=amount,
            quantity=1,
            status=status,
            created_at=created_at,
            amount_mismatch=amount_mismatch,
        )
        session.add(payment)
        session.flush()
        return payment.id


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


def _all_runs(db: Database) -> list[BankReconciliationRun]:
    with db.session() as session:
        return list(
            session.execute(select(BankReconciliationRun).order_by(BankReconciliationRun.id))
            .scalars()
            .all()
        )


def test_reconcile_with_no_candidates_records_success_run_with_zero_counts(
    db: Database, settings: Settings
) -> None:
    outcomes = svc.reconcile(db, settings)

    assert outcomes == []
    runs = _all_runs(db)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == BankReconciliationRunStatus.SUCCESS
    assert run.candidates_checked == 0
    assert run.entries_fetched is None
    assert run.matched_count == 0
    assert run.mismatch_count == 0
    assert run.ttl_expired_count == 0
    assert run.finalize_error_count == 0
    assert run.error_message is None
    assert run.finished_at is not None


def test_reconcile_records_counts_for_matched_and_mismatched_payments(
    db: Database, settings: Settings, monkeypatch
) -> None:
    gid = make_giveaway(db)
    pid = make_participant(db)
    matched_outcome = make_pending_payment(db, giveaway_id=gid, participant_id=pid, quantity=2)
    mismatched_outcome = make_pending_payment(db, giveaway_id=gid, participant_id=pid, quantity=1)
    assert matched_outcome.ok and matched_outcome.invoice_no is not None
    assert mismatched_outcome.ok and mismatched_outcome.invoice_no is not None

    entries = [
        BankStatementEntry(
            external_id="op-1",
            amount=matched_outcome.amount or 0,
            purpose=f"Оплата по счету № {matched_outcome.invoice_no} от 22.07.2026",
            operation_date=utcnow(),
        ),
        BankStatementEntry(
            external_id="op-2",
            amount=(mismatched_outcome.amount or 0) - 1,
            purpose=f"Оплата по счету № {mismatched_outcome.invoice_no} от 22.07.2026",
            operation_date=utcnow(),
        ),
    ]
    settings._fake_entries = entries  # type: ignore[attr-defined]
    monkeypatch.setattr(svc, "TBankStatementProvider", _FakeStatementProvider)

    svc.reconcile(db, settings)

    runs = _all_runs(db)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == BankReconciliationRunStatus.SUCCESS
    assert run.candidates_checked == 2
    assert run.entries_fetched == 2
    assert run.matched_count == 1
    assert run.mismatch_count == 1
    assert run.ttl_expired_count == 0
    assert run.finalize_error_count == 0


def test_reconcile_records_fetch_failed_run_with_error_message(
    db: Database, settings: Settings, monkeypatch
) -> None:
    gid = make_giveaway(db)
    pid = make_participant(db)
    outcome = make_pending_payment(db, giveaway_id=gid, participant_id=pid, quantity=1)
    assert outcome.ok

    class _FailingStatementProvider:
        @classmethod
        def from_settings(cls, settings: Settings) -> _FailingStatementProvider:
            return cls()

        def fetch_operations(self, *, since: dt.datetime) -> list[BankStatementEntry]:
            raise RuntimeError("T-Bank API недоступен")

    monkeypatch.setattr(svc, "TBankStatementProvider", _FailingStatementProvider)

    outcomes = svc.reconcile(db, settings)

    assert outcomes == []
    runs = _all_runs(db)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == BankReconciliationRunStatus.FETCH_FAILED
    assert run.candidates_checked == 1
    assert run.entries_fetched is None
    assert run.error_message is not None
    assert "T-Bank API недоступен" in run.error_message
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.PENDING


def test_reconcile_run_retention_deletes_old_rows(db: Database, settings: Settings) -> None:
    settings.bank_reconciliation_run_retention_days = 1
    old_run = BankReconciliationRun(
        started_at=utcnow() - dt.timedelta(days=2),
        finished_at=utcnow() - dt.timedelta(days=2),
        status=BankReconciliationRunStatus.SUCCESS,
        candidates_checked=0,
        entries_fetched=None,
    )
    with db.session() as session:
        session.add(old_run)

    svc.reconcile(db, settings)

    runs = _all_runs(db)
    assert len(runs) == 1
    assert runs[0].candidates_checked == 0
    assert (utcnow() - runs[0].started_at) < dt.timedelta(minutes=1)


def test_get_reconciliation_status_is_stale_when_no_runs_exist(
    db: Database, settings: Settings
) -> None:
    status = svc.get_reconciliation_status(db, settings)

    assert status.runs == []
    assert status.is_stale is True
    assert status.last_success_at is None
    assert status.total_runs_24h == 0
    assert status.failed_runs_24h == 0


def test_get_reconciliation_status_reports_recent_runs_and_aggregates(
    db: Database, settings: Settings
) -> None:
    now = utcnow()
    # Порог "устарело" — 2x интервал опроса (см. is_stale); поднимаем интервал,
    # чтобы последний тик "5 минут назад" ниже не считался устаревшим.
    settings.online_status_poll_interval_sec = 600
    with db.session() as session:
        session.add(
            BankReconciliationRun(
                started_at=now - dt.timedelta(minutes=10),
                finished_at=now - dt.timedelta(minutes=10),
                status=BankReconciliationRunStatus.FETCH_FAILED,
                candidates_checked=1,
                entries_fetched=None,
                error_message="boom",
            )
        )
        session.add(
            BankReconciliationRun(
                started_at=now - dt.timedelta(minutes=5),
                finished_at=now - dt.timedelta(minutes=5),
                status=BankReconciliationRunStatus.SUCCESS,
                candidates_checked=0,
                entries_fetched=0,
            )
        )

    status = svc.get_reconciliation_status(db, settings, now=now)

    assert len(status.runs) == 2
    assert status.runs[0].status == BankReconciliationRunStatus.SUCCESS  # самый новый первым
    assert status.total_runs_24h == 2
    assert status.failed_runs_24h == 1
    assert status.last_success_at == now - dt.timedelta(minutes=5)
    assert status.is_stale is False


def test_get_reconciliation_status_is_stale_when_last_run_too_old(
    db: Database, settings: Settings
) -> None:
    now = utcnow()
    settings.online_status_poll_interval_sec = 60
    with db.session() as session:
        session.add(
            BankReconciliationRun(
                started_at=now - dt.timedelta(minutes=10),
                finished_at=now - dt.timedelta(minutes=10),
                status=BankReconciliationRunStatus.SUCCESS,
                candidates_checked=0,
                entries_fetched=0,
            )
        )

    status = svc.get_reconciliation_status(db, settings, now=now)

    assert status.is_stale is True


def test_get_payments_brief_buckets_by_creation_day_and_status(db: Database) -> None:
    gid = make_giveaway(db)
    pid = make_participant(db)
    now = utcnow()
    today_start = dt.datetime.combine(now.date(), dt.time.min)

    # Сегодня: успешный, ожидающий, спорный (расхождение суммы) + один ровно на
    # границе полуночи (проверка включительно/исключительно).
    make_raw_payment(
        db,
        giveaway_id=gid,
        participant_id=pid,
        created_at=today_start + dt.timedelta(hours=1),
        status=PaymentStatus.SUCCEEDED,
        amount=10000,
    )
    make_raw_payment(
        db,
        giveaway_id=gid,
        participant_id=pid,
        created_at=today_start + dt.timedelta(hours=2),
        status=PaymentStatus.PENDING,
        amount=20000,
    )
    make_raw_payment(
        db,
        giveaway_id=gid,
        participant_id=pid,
        created_at=today_start + dt.timedelta(hours=3),
        status=PaymentStatus.PENDING,
        amount=30000,
        amount_mismatch=True,
    )
    make_raw_payment(
        db,
        giveaway_id=gid,
        participant_id=pid,
        created_at=today_start,  # ровно полночь — должен попасть в "сегодня"
        status=PaymentStatus.PENDING,
        amount=1000,
    )

    # Вчера: один успешный (не должен попасть в "сегодня").
    make_raw_payment(
        db,
        giveaway_id=gid,
        participant_id=pid,
        created_at=today_start - dt.timedelta(hours=2),
        status=PaymentStatus.SUCCEEDED,
        amount=5000,
    )

    # Позавчера — не должен попасть ни в "сегодня", ни в "вчера".
    make_raw_payment(
        db,
        giveaway_id=gid,
        participant_id=pid,
        created_at=today_start - dt.timedelta(days=2),
        status=PaymentStatus.SUCCEEDED,
        amount=99999,
    )

    # Другой провайдер сегодня — не должен попасть в бриф (только requisites_qr).
    make_raw_payment(
        db,
        giveaway_id=gid,
        participant_id=pid,
        created_at=today_start + dt.timedelta(hours=1),
        status=PaymentStatus.SUCCEEDED,
        provider=PaymentProviderType.MOCK,
        amount=77777,
    )

    brief = svc.get_payments_brief(db, now=now)

    assert brief.today.total_count == 4
    assert brief.today.total_amount == 61000
    assert brief.today.succeeded_count == 1
    assert brief.today.succeeded_amount == 10000
    assert brief.today.pending_count == 2
    assert brief.today.pending_amount == 21000
    assert brief.today.disputed_count == 1
    assert brief.today.disputed_amount == 30000

    assert brief.yesterday.total_count == 1
    assert brief.yesterday.total_amount == 5000
    assert brief.yesterday.succeeded_count == 1
    assert brief.yesterday.succeeded_amount == 5000
    assert brief.yesterday.pending_count == 0
    assert brief.yesterday.disputed_count == 0


def test_get_payments_brief_returns_zeroed_cohorts_when_no_payments(db: Database) -> None:
    brief = svc.get_payments_brief(db)

    for cohort in (brief.today, brief.yesterday):
        assert cohort.total_count == 0
        assert cohort.total_amount == 0
        assert cohort.succeeded_count == 0
        assert cohort.pending_count == 0
        assert cohort.disputed_count == 0
