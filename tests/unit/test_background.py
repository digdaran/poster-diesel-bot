"""Тесты фоновой сверки платежей и освобождения просроченных резервов
(п.7.5, 9, 20.1 ТЗ). До этого модуля poll_pending_payment/
find_expired_reservation_refs существовали и были покрыты тестами по
отдельности, но их никто не вызывал за пределами тестов — из-за чего
платежи и ручные регистрации оставались в PENDING бессрочно."""

from __future__ import annotations

import datetime as dt

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import ManualRegistrationStatus, PanelUserRole, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.payments.mock import MockProvider
from app.services import manual_registration_service as manual_svc
from app.services import payment_service as payment_svc
from app.services import ticket_pool_service as pool_svc
from backend import background
from sqlalchemy import select


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


def test_reconcile_pending_payments_fails_and_releases_expired(
    db: Database, settings: Settings
) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = payment_svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert outcome.ok
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 7

    far_future = utcnow() + dt.timedelta(seconds=settings.online_reservation_ttl_sec + 100)
    outcomes = background._reconcile_pending_payments(db, settings, now=far_future)

    assert len(outcomes) == 1
    assert outcomes[0].applied
    assert outcomes[0].new_status == PaymentStatus.FAILED
    assert outcomes[0].payment_id == outcome.payment_id

    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.FAILED
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10


def test_reconcile_pending_payments_leaves_fresh_payment_alone(
    db: Database, settings: Settings
) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = payment_svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert outcome.ok

    background._reconcile_pending_payments(db, settings, now=utcnow())

    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.PENDING
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 7  # резерв не тронут


def test_reconcile_pending_payments_returns_succeeded_outcome(
    db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_reconcile_pending_payments` возвращает финализированные исходы (в т.ч.
    успешные) — их использует `run_background_loop` для проактивных уведомлений
    (см. app/services/notification_service.py, DECISIONS.md)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = payment_svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok
    provider.set_status(outcome.order_id, PaymentStatus.SUCCEEDED)
    # create_provider создаёт новый инстанс провайдера при каждом вызове (см.
    # app/payments/factory.py) — для теста подменяем его на уже настроенный
    # provider с проставленным статусом, независимо от запрошенного типа.
    monkeypatch.setattr(background, "create_provider", lambda settings, provider_type: provider)

    outcomes = background._reconcile_pending_payments(db, settings, now=utcnow())

    assert len(outcomes) == 1
    assert outcomes[0].applied
    assert outcomes[0].new_status == PaymentStatus.SUCCEEDED
    assert len(outcomes[0].tickets or []) == 2


def test_reconcile_pending_payments_uses_each_payments_own_provider(
    db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регресс: раньше сверка резолвила ОДИН "активный" провайдер для ВСЕХ
    PENDING-платежей разом — если активный провайдер сменился (в панели) после
    создания платежа, статус проверялся через не тот банк, и платёж навсегда
    оставался в PENDING (ровно так пользователь описал зависший T-Bank платёж).
    Теперь для каждого платежа должен резолвиться его собственный `Payment.provider`."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = payment_svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok

    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        recorded_provider_type = payment.provider

    seen_provider_types = []

    def fake_create_provider(settings, provider_type):  # type: ignore[no-untyped-def]
        seen_provider_types.append(provider_type)
        return MockProvider()

    monkeypatch.setattr(background, "create_provider", fake_create_provider)

    background._reconcile_pending_payments(db, settings, now=utcnow())

    assert seen_provider_types == [recorded_provider_type]


def test_reconcile_pending_payments_noop_when_none_pending(
    db: Database, settings: Settings
) -> None:
    background._reconcile_pending_payments(db, settings, now=utcnow())  # не должно падать


def test_release_expired_manual_registrations_cancels_and_frees(
    db: Database, settings: Settings
) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    operator_id = make_operator(db)
    outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=4,
        operator_id=operator_id,
        ttl_seconds=settings.manual_reservation_ttl_sec,
    )
    assert outcome.ok
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 6

    far_future = utcnow() + dt.timedelta(seconds=settings.manual_reservation_ttl_sec + 100)
    background._release_expired_manual_registrations(db, settings, now=far_future)

    with db.session() as session:
        registration = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        assert registration.status == ManualRegistrationStatus.CANCELLED
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10


def test_release_expired_manual_registrations_leaves_fresh_one_alone(
    db: Database, settings: Settings
) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    operator_id = make_operator(db)
    outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=4,
        operator_id=operator_id,
        ttl_seconds=settings.manual_reservation_ttl_sec,
    )
    assert outcome.ok

    background._release_expired_manual_registrations(db, settings, now=utcnow())

    with db.session() as session:
        registration = session.execute(
            select(ManualRegistration).where(
                ManualRegistration.id == outcome.manual_registration_id
            )
        ).scalar_one()
        assert registration.status == ManualRegistrationStatus.PENDING
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 6
