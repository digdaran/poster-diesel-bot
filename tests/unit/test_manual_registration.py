"""Тесты ручных регистраций (п.7.5, 7.7, 8.2, 20.1 ТЗ): создание с резервом,
подтверждение и выдача, запрет повторного подтверждения, отмена только до
подтверждения, нехватка номеров."""

from __future__ import annotations

import pytest
from app.core.db import Database
from app.models.enums import ManualRegistrationStatus, PanelUserRole
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import manual_registration_service as svc
from app.services import payment_service as payment_svc
from app.services import ticket_pool_service as pool_svc
from sqlalchemy import select


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
    """Лимит суммарного количества экземпляров (DECISIONS.md №45, отменяет
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
