"""Тесты ручных регистраций (п.7.5, 7.7, 8.2, 20.1 ТЗ): создание с резервом,
подтверждение и выдача, запрет повторного подтверждения, отмена только до
подтверждения, нехватка номеров."""

from __future__ import annotations

import datetime as dt

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import (
    ManualRegistrationPaymentMethod,
    ManualRegistrationStatus,
    PanelUserRole,
)
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.ticket_pool import TicketPool
from app.payments.bank_statement import BankStatementEntry
from app.payments.requisites_qr import RequisitesQrProvider
from app.repositories import ticket_pool_repo
from app.services import bank_reconciliation_service
from app.services import manual_registration_service as svc
from app.services import payment_service as payment_svc
from app.services import ticket_pool_service as pool_svc
from sqlalchemy import select


class _FakeStatementProvider:
    def __init__(self, entries: list[BankStatementEntry]) -> None:
        self._entries = entries

    def fetch_operations(self, *, since: dt.datetime) -> list[BankStatementEntry]:
        return self._entries

    @classmethod
    def from_settings(cls, settings: Settings) -> _FakeStatementProvider:
        return cls(getattr(settings, "_fake_entries", []))


def make_provider() -> RequisitesQrProvider:
    return RequisitesQrProvider(
        recipient_name="ИП Тест",
        recipient_inn="770101001770",
        recipient_kpp="",
        personal_acc="40802810000000000001",
        bank_name="Тестбанк",
        bic="044525225",
        corresp_acc="30101810000000000225",
        vat_rate_percent=0,
    )


def make_giveaway(db: Database, *, max_tickets: int = 10, prefix: str = "AUG") -> int:
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


def make_operator(db: Database, login: str = "operator1") -> int:
    with db.session() as session:
        u = PanelUser(login=login, password_hash="x", role=PanelUserRole.OPERATOR)
        session.add(u)
        session.flush()
        return u.id


def test_create_reserves_and_confirm_issues_tickets(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)

    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=3, operator_id=oid, ttl_seconds=3600
    )
    assert outcome.ok
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 7

    confirm = svc.confirm_manual_registration(
        db, manual_registration_id=outcome.manual_registration_id
    )
    assert len(confirm.tickets) == 3

    with db.session() as session:
        reg = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        assert reg.status == ManualRegistrationStatus.CONFIRMED
        assert reg.confirmed_at is not None
        g = session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()
        assert g.tickets_issued == 3
        assert g.tickets_reserved == 0


def test_insufficient_tickets_registration_not_created(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=2)
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=5, operator_id=oid, ttl_seconds=3600
    )
    assert not outcome.ok
    assert outcome.free_count == 2
    with db.session() as session:
        count = len(list(session.execute(select(ManualRegistration)).scalars()))
        assert count == 0


def test_create_rejects_blocked_participant(db: Database) -> None:
    """Регресс: заблокированный (Participant.is_blocked) участник не должен
    получать номера даже через ручную регистрацию оператором."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    with db.session() as session:
        participant = session.get(Participant, pid)
        assert participant is not None
        participant.is_blocked = True

    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    assert not outcome.ok
    assert outcome.participant_blocked
    with db.session() as session:
        count = len(list(session.execute(select(ManualRegistration)).scalars()))
        assert count == 0


def test_create_blocked_by_existing_pending_manual_registration(db: Database) -> None:
    """Лимит суммарного количества экземпляров (DECISIONS_LOG.md №45, отменяет
    бинарное правило №22) — занижаем лимит для теста."""
    db.settings.max_pending_tickets_per_participant = 1
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    first = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    assert first.ok

    second = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    assert not second.ok
    assert second.pending_limit_exceeded
    with db.session() as session:
        count = len(list(session.execute(select(ManualRegistration)).scalars()))
        assert count == 1


def test_create_blocked_by_existing_pending_payment(db: Database) -> None:
    db.settings.max_pending_tickets_per_participant = 1
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    payment_outcome = payment_svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert payment_outcome.ok

    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    assert not outcome.ok
    assert outcome.pending_limit_exceeded
    with db.session() as session:
        count = len(list(session.execute(select(ManualRegistration)).scalars()))
        assert count == 0


def test_repeated_confirmation_forbidden(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=2, operator_id=oid, ttl_seconds=3600
    )
    svc.confirm_manual_registration(db, manual_registration_id=outcome.manual_registration_id)
    with pytest.raises(svc.ManualRegistrationStateError):
        svc.confirm_manual_registration(db, manual_registration_id=outcome.manual_registration_id)
    # Номерки не выданы повторно
    with db.session() as session:
        g = session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()
        assert g.tickets_issued == 2


def test_cancel_before_confirmation_releases_reservation(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=4, operator_id=oid, ttl_seconds=3600
    )
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 6
    svc.cancel_manual_registration(db, manual_registration_id=outcome.manual_registration_id)
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10
    with db.session() as session:
        reg = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        assert reg.status == ManualRegistrationStatus.CANCELLED
        assert reg.cancelled_at is not None


def test_cancel_after_confirmation_forbidden(db: Database) -> None:
    """Отмена подтверждённой регистрации в первой версии не предусмотрена (п.7.7, 21 ТЗ)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=2, operator_id=oid, ttl_seconds=3600
    )
    svc.confirm_manual_registration(db, manual_registration_id=outcome.manual_registration_id)
    with pytest.raises(svc.ManualRegistrationStateError):
        svc.cancel_manual_registration(db, manual_registration_id=outcome.manual_registration_id)


def test_cancel_already_cancelled_forbidden(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=2, operator_id=oid, ttl_seconds=3600
    )
    svc.cancel_manual_registration(db, manual_registration_id=outcome.manual_registration_id)
    with pytest.raises(svc.ManualRegistrationStateError):
        svc.cancel_manual_registration(db, manual_registration_id=outcome.manual_registration_id)


def test_generate_qr_assigns_invoice_and_marks_cashless(db: Database, settings: Settings) -> None:
    gid = make_giveaway(db, max_tickets=10, prefix="QRT")
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=2, operator_id=oid, ttl_seconds=3600
    )
    assert outcome.ok

    result = svc.generate_manual_registration_qr(
        db, settings, manual_registration_id=outcome.manual_registration_id
    )
    assert result.invoice_no == "QRT-00001"
    assert result.amount == 20000  # 2 * ticket_price(10000)
    assert "ST00012|" in result.qr_code_payload
    assert "QRT-00001" in result.qr_code_payload

    with db.session() as session:
        reg = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        assert reg.payment_method == ManualRegistrationPaymentMethod.CASHLESS
        assert reg.payment_number == 1
        assert reg.qr_code_payload == result.qr_code_payload
        assert reg.qr_generated_at is not None
        # Резерв в пуле не тронут генерацией QR — регистрация всё ещё PENDING.
        assert reg.status == ManualRegistrationStatus.PENDING
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 8


def test_generate_qr_is_idempotent_does_not_reuse_counter_twice(
    db: Database, settings: Settings
) -> None:
    gid = make_giveaway(db, max_tickets=10, prefix="QRI")
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    first = svc.generate_manual_registration_qr(
        db, settings, manual_registration_id=outcome.manual_registration_id
    )
    second = svc.generate_manual_registration_qr(
        db, settings, manual_registration_id=outcome.manual_registration_id
    )
    assert second.invoice_no == first.invoice_no
    assert second.qr_code_payload == first.qr_code_payload

    with db.session() as session:
        g = session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()
        assert g.next_payment_number == 2  # инкремент произошёл только один раз


def test_generate_qr_shares_invoice_counter_with_online_payments(
    db: Database, settings: Settings
) -> None:
    """PREFIX-NNNNN должен быть уникален в рамках розыгрыша независимо от
    источника (online Payment vs ручная регистрация) — см. DECISIONS.md, оба
    берут номер из одного Giveaway.next_payment_number."""
    gid = make_giveaway(db, max_tickets=10, prefix="QRS")
    pid = make_participant(db)
    oid = make_operator(db)

    payment_outcome = payment_svc.create_payment_safe(
        db,
        make_provider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert payment_outcome.ok
    assert payment_outcome.invoice_no == "QRS-00001"

    manual_outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    assert manual_outcome.ok
    qr_result = svc.generate_manual_registration_qr(
        db, settings, manual_registration_id=manual_outcome.manual_registration_id
    )
    assert qr_result.invoice_no == "QRS-00002"


def test_generate_qr_forbidden_after_confirmation(db: Database, settings: Settings) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    svc.confirm_manual_registration(db, manual_registration_id=outcome.manual_registration_id)
    with pytest.raises(svc.ManualRegistrationStateError):
        svc.generate_manual_registration_qr(
            db, settings, manual_registration_id=outcome.manual_registration_id
        )


def test_generate_qr_missing_registration_raises(db: Database, settings: Settings) -> None:
    with pytest.raises(svc.ManualRegistrationStateError):
        svc.generate_manual_registration_qr(db, settings, manual_registration_id=999999)


def test_switch_to_cash_after_qr_reverts_payment_method(db: Database, settings: Settings) -> None:
    """Покупатель не смог/не захотел оплатить по уже сформированному QR и решил
    заплатить оператору наличными (см. DECISIONS.md)."""
    gid = make_giveaway(db, max_tickets=10, prefix="CSH")
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    qr_result = svc.generate_manual_registration_qr(
        db, settings, manual_registration_id=outcome.manual_registration_id
    )

    svc.switch_manual_registration_to_cash(
        db, settings, manual_registration_id=outcome.manual_registration_id
    )

    with db.session() as session:
        reg = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        assert reg.payment_method == ManualRegistrationPaymentMethod.CASH
        assert reg.status == ManualRegistrationStatus.PENDING
        # Номер счёта/QR не стираются — просто больше не используются.
        assert reg.qr_code_payload is not None

    # Подтверждение по-прежнему работает как обычное наличное.
    confirm = svc.confirm_manual_registration(
        db, manual_registration_id=outcome.manual_registration_id
    )
    assert len(confirm.tickets) == 1
    assert qr_result.invoice_no  # sanity: QR действительно был сформирован до переключения


def test_switch_to_cash_forbidden_after_confirmation(db: Database, settings: Settings) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    svc.generate_manual_registration_qr(
        db, settings, manual_registration_id=outcome.manual_registration_id
    )
    svc.confirm_manual_registration(db, manual_registration_id=outcome.manual_registration_id)
    with pytest.raises(svc.ManualRegistrationStateError):
        svc.switch_manual_registration_to_cash(
            db, settings, manual_registration_id=outcome.manual_registration_id
        )


def test_switch_to_cash_missing_registration_raises(db: Database, settings: Settings) -> None:
    with pytest.raises(svc.ManualRegistrationStateError):
        svc.switch_manual_registration_to_cash(db, settings, manual_registration_id=999999)


def test_switched_to_cash_registration_excluded_from_reconciliation_candidates(
    db: Database, settings: Settings, monkeypatch
) -> None:
    """После возврата к наличным регистрация не должна больше подхватываться
    фоновой сверкой выписки (та матчит только payment_method=CASHLESS)."""
    gid = make_giveaway(db, max_tickets=10, prefix="SWC")
    pid = make_participant(db)
    oid = make_operator(db)
    outcome = svc.create_manual_registration_safe(
        db, giveaway_id=gid, participant_id=pid, quantity=1, operator_id=oid, ttl_seconds=3600
    )
    qr_result = svc.generate_manual_registration_qr(
        db, settings, manual_registration_id=outcome.manual_registration_id
    )
    svc.switch_manual_registration_to_cash(
        db, settings, manual_registration_id=outcome.manual_registration_id
    )

    entries = [
        BankStatementEntry(
            external_id="op-1",
            amount=qr_result.amount,
            purpose=f"Оплата по счету № {qr_result.invoice_no} от 22.07.2026",
            operation_date=utcnow(),
        )
    ]
    settings._fake_entries = entries  # type: ignore[attr-defined]
    monkeypatch.setattr(
        bank_reconciliation_service, "TBankStatementProvider", _FakeStatementProvider
    )

    bank_reconciliation_service.reconcile(db, settings)

    with db.session() as session:
        reg = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        # Всё ещё PENDING/CASH — сверка не подтвердила её "за компанию".
        assert reg.status == ManualRegistrationStatus.PENDING
        assert reg.payment_method == ManualRegistrationPaymentMethod.CASH


def _reserved_until(db: Database, *, manual_registration_id: int) -> dt.datetime:
    with db.session() as session:
        row = (
            session.execute(
                select(TicketPool).where(
                    TicketPool.manual_registration_id == manual_registration_id
                )
            )
            .scalars()
            .first()
        )
        assert row is not None
        assert row.reserved_until is not None
        return row.reserved_until


def test_generate_qr_extends_reservation_past_short_manual_ttl(
    db: Database, settings: Settings
) -> None:
    """QR подразумевает банковский перевод, который может идти дольше, чем
    короткий TTL наличных (см. DECISIONS.md) — резерв номерков должен пережить
    момент, когда MANUAL_RESERVATION_TTL_SEC уже истёк бы, но
    REQUISITES_INVOICE_TTL_DAYS ещё нет."""
    settings.manual_reservation_ttl_sec = 3600  # 1 час, как дефолт наличных
    settings.requisites_invoice_ttl_days = 10
    gid = make_giveaway(db, max_tickets=10, prefix="TTL")
    pid = make_participant(db)
    oid = make_operator(db)
    now = utcnow()
    outcome = svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=1,
        operator_id=oid,
        ttl_seconds=settings.manual_reservation_ttl_sec,
    )
    reg_id = outcome.manual_registration_id
    assert reg_id is not None

    svc.generate_manual_registration_qr(db, settings, manual_registration_id=reg_id, now=now)
    reserved_until = _reserved_until(db, manual_registration_id=reg_id)
    assert reserved_until == now + dt.timedelta(days=10)

    # Момент, когда наличный TTL (1 час) давно истёк бы, но QR-TTL (10 дней) — нет.
    past_manual_ttl = now + dt.timedelta(hours=2)
    with db.session() as session:
        refs = ticket_pool_repo.find_expired_reservation_refs(session, now=past_manual_ttl)
    assert ("manual", reg_id) not in refs


def test_switch_to_cash_shortens_reservation_back_to_manual_ttl(
    db: Database, settings: Settings
) -> None:
    settings.manual_reservation_ttl_sec = 1800  # 30 минут
    settings.requisites_invoice_ttl_days = 10
    gid = make_giveaway(db, max_tickets=10, prefix="TTS")
    pid = make_participant(db)
    oid = make_operator(db)
    now = utcnow()
    outcome = svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=1,
        operator_id=oid,
        ttl_seconds=settings.manual_reservation_ttl_sec,
    )
    reg_id = outcome.manual_registration_id
    assert reg_id is not None
    svc.generate_manual_registration_qr(db, settings, manual_registration_id=reg_id, now=now)
    assert _reserved_until(db, manual_registration_id=reg_id) == now + dt.timedelta(days=10)

    switch_now = now + dt.timedelta(hours=1)
    svc.switch_manual_registration_to_cash(
        db, settings, manual_registration_id=reg_id, now=switch_now
    )

    reserved_until = _reserved_until(db, manual_registration_id=reg_id)
    assert reserved_until == switch_now + dt.timedelta(seconds=1800)

    # Короткий наличный TTL снова истекает как обычно.
    with db.session() as session:
        refs = ticket_pool_repo.find_expired_reservation_refs(
            session, now=switch_now + dt.timedelta(minutes=31)
        )
    assert ("manual", reg_id) in refs
