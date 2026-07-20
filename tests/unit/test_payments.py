"""Тесты платежей и идемпотентности (п.7.6, 9, 20.1 ТЗ):
повторный webhook, неверная подпись, неизвестный заказ, отказ в оплате,
гонка webhook/фоновой сверки, TTL/лимит попыток."""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import PanelUserRole, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.panel_user import PanelUser
from app.models.participant import Participant
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.payments.base import WebhookVerificationError
from app.payments.mock import MockProvider
from app.services import manual_registration_service as manual_svc
from app.services import payment_service as svc
from app.services import ticket_pool_service as pool_svc
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


def test_create_payment_reserves_tickets(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert outcome.ok
    assert outcome.created is not None
    assert outcome.amount == 3 * 10000  # quantity * ticket_price (см. make_giveaway)
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 7


def test_create_payment_persists_external_payment_id(db: Database) -> None:
    """Регресс на боевой инцидент (см. DECISIONS.md): без сохранённого
    `external_payment_id` резервная проверка статуса у Т-Банк невозможна вообще
    (GetState требует именно его, не order_id)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok
    assert outcome.created is not None
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.external_payment_id == outcome.created.external_payment_id
        assert payment.external_payment_id is not None


def test_poll_pending_payment_passes_external_payment_id_to_provider(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert outcome.ok
    assert outcome.created is not None

    seen_calls: list[tuple[str, str | None]] = []

    class SpyProvider(MockProvider):
        def check_status(self, order_id, *, external_payment_id=None):  # type: ignore[no-untyped-def]
            seen_calls.append((order_id, external_payment_id))
            return super().check_status(order_id, external_payment_id=external_payment_id)

    svc.poll_pending_payment(
        db, SpyProvider(), payment_id=outcome.payment_id, max_attempts=10, ttl_seconds=600
    )

    assert seen_calls == [(outcome.order_id, outcome.created.external_payment_id)]


def test_create_payment_persists_payment_url_and_qr(db: Database) -> None:
    """payment_url/qr_code_payload — one-shot данные от провайдера (см.
    CreatedPayment) — должны сохраняться на Payment, а не оставаться только в
    транзиентном outcome, иначе кнопка «Показать QR» в боте не сможет их
    получить позже отдельным событием (см. DECISIONS.md)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    outcome = svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok
    assert outcome.payment_id is not None
    assert outcome.created is not None
    with db.session() as session:
        payment = session.get(Payment, outcome.payment_id)
        assert payment is not None
        assert payment.payment_url == outcome.created.payment_url
        assert payment.qr_code_payload == outcome.created.qr_code_payload
        assert payment.qr_code_payload is not None  # MockProvider всегда отдаёт QR


def test_create_payment_insufficient_tickets_not_created(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=2)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=5,
    )
    assert not outcome.ok
    assert outcome.free_count == 2
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 0  # платёж не создан вовсе


def test_create_payment_blocked_by_existing_pending_payment(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    first = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert first.ok

    second = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert not second.ok
    assert second.has_active_purchase
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 1  # второй платёж не создан
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 9  # резерв под второй не создан


def test_create_payment_blocked_by_existing_pending_manual_registration(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    with db.session() as session:
        operator = PanelUser(login="op1", password_hash="x", role=PanelUserRole.OPERATOR)
        session.add(operator)
        session.flush()
        operator_id = operator.id

    manual_outcome = manual_svc.create_manual_registration_safe(
        db,
        giveaway_id=gid,
        participant_id=pid,
        quantity=1,
        operator_id=operator_id,
        ttl_seconds=3600,
    )
    assert manual_outcome.ok

    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=1,
    )
    assert not outcome.ok
    assert outcome.has_active_purchase
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 0


def test_create_payment_concurrent_same_participant_only_one_succeeds(db: Database) -> None:
    """Гонка: участник одновременно пытается купить в двух разных розыгрышах —
    ровно одна покупка должна стать активной (глобальное правило, см. DECISIONS.md)."""
    gid_a = make_giveaway(db, max_tickets=10, prefix="AAA")
    gid_b = make_giveaway(db, max_tickets=10, prefix="BBB")
    pid = make_participant(db)
    provider = MockProvider()

    outcomes: dict[str, svc.CreatePaymentOutcome] = {}
    barrier = threading.Barrier(2)

    def worker(name: str, gid: int) -> None:
        barrier.wait()
        outcomes[name] = svc.create_payment_safe(
            db,
            provider,
            giveaway_id=gid,
            participant_id=pid,
            participant_phone="79991234567",
            quantity=1,
        )

    t1 = threading.Thread(target=worker, args=("A", gid_a))
    t2 = threading.Thread(target=worker, args=("B", gid_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    successes = [o for o in outcomes.values() if o.ok]
    blocked = [o for o in outcomes.values() if not o.ok and o.has_active_purchase]
    assert len(successes) == 1
    assert len(blocked) == 1
    with db.session() as session:
        count = len(list(session.execute(select(Payment)).scalars()))
        assert count == 1


def test_finalize_success_issues_tickets(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=3,
    )
    assert outcome.ok
    result = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
    assert result.applied
    assert len(result.tickets) == 3
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.SUCCEEDED
        tickets = list(
            session.execute(select(Ticket).where(Ticket.payment_id == outcome.payment_id)).scalars()
        )
        assert len(tickets) == 3
        assert {t.full_code for t in tickets} == {
            t.full_code for t in tickets
        }  # уникальны по конструкции
        g = session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()
        assert g.tickets_issued == 3
        assert g.tickets_reserved == 0


def test_finalize_failed_releases_reservation(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=4,
    )
    assert outcome.ok
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 6
    result = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.FAILED)
    assert result.applied
    assert result.tickets == []
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10


def test_repeated_webhook_is_noop(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    first = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
    assert first.applied
    second = svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
    assert not second.applied  # повторный webhook — no-op, без повторной выдачи
    with db.session() as session:
        tickets = list(
            session.execute(select(Ticket).where(Ticket.payment_id == outcome.payment_id)).scalars()
        )
        assert len(tickets) == 2  # не удвоилось


def test_finalize_unknown_order_id_is_noop(db: Database) -> None:
    result = svc.finalize_payment(db, order_id="does-not-exist", new_status=PaymentStatus.SUCCEEDED)
    assert not result.applied


def test_webhook_invalid_signature_rejected(db: Database) -> None:
    provider = MockProvider()
    body = provider.build_webhook_payload("some-order", PaymentStatus.SUCCEEDED)
    with pytest.raises(WebhookVerificationError):
        provider.verify_and_parse_webhook(headers={"X-Mock-Signature": "wrong"}, body=body)


def test_webhook_valid_signature_parsed(db: Database) -> None:
    provider = MockProvider()
    body = provider.build_webhook_payload("some-order", PaymentStatus.SUCCEEDED)
    headers = provider.sign_headers(body)
    event = provider.verify_and_parse_webhook(headers=headers, body=body)
    assert event.order_id == "some-order"
    assert event.status == PaymentStatus.SUCCEEDED


def test_finalize_via_webhook_and_background_poll_race_only_one_applies(db: Database) -> None:
    """Гонка: webhook и фоновая сверка одновременно финализируют один платёж —
    ровно одна сторона выполняет переход, вторая получает no-op (п.7.6 ТЗ)."""
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert outcome.ok

    results: list[svc.FinalizeOutcome] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        results.append(
            svc.finalize_payment(db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED)
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    applied = [r for r in results if r.applied]
    noop = [r for r in results if not r.applied]
    assert len(applied) == 1
    assert len(noop) == 1

    with db.session() as session:
        tickets = list(
            session.execute(select(Ticket).where(Ticket.payment_id == outcome.payment_id)).scalars()
        )
        assert len(tickets) == 2  # выдано ровно один раз


def test_poll_pending_payment_finalizes_on_bank_success(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    provider.set_status(outcome.order_id, PaymentStatus.SUCCEEDED)

    result = svc.poll_pending_payment(
        db, provider, payment_id=outcome.payment_id, max_attempts=10, ttl_seconds=600
    )
    assert result is not None
    assert result.outcome is not None
    assert result.outcome.applied
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.status == PaymentStatus.SUCCEEDED


def test_poll_pending_payment_still_pending_increments_attempts(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    # provider ещё PENDING (банк не ответил)
    result = svc.poll_pending_payment(
        db, provider, payment_id=outcome.payment_id, max_attempts=10, ttl_seconds=600
    )
    assert result is not None
    assert result.outcome is None
    with db.session() as session:
        payment = session.execute(
            select(Payment).where(Payment.id == outcome.payment_id)
        ).scalar_one()
        assert payment.poll_attempts == 1
        assert payment.status == PaymentStatus.PENDING


def test_poll_pending_payment_ttl_fallback_releases_and_fails(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 8

    far_future = utcnow() + dt.timedelta(seconds=700)
    result = svc.poll_pending_payment(
        db,
        provider,
        payment_id=outcome.payment_id,
        max_attempts=10,
        ttl_seconds=600,
        now=far_future,
    )
    assert result is not None
    assert result.outcome is not None
    assert result.outcome.applied
    assert result.outcome.new_status == PaymentStatus.FAILED
    assert pool_svc.get_free_count(db, giveaway_id=gid) == 10


def test_poll_pending_payment_max_attempts_exhausted(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    provider = MockProvider()
    outcome = svc.create_payment_safe(
        db,
        provider,
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79991234567",
        quantity=2,
    )
    with db.session() as session:
        session.execute(
            Payment.__table__.update()
            .where(Payment.id == outcome.payment_id)
            .values(poll_attempts=9)
        )
    result = svc.poll_pending_payment(
        db, provider, payment_id=outcome.payment_id, max_attempts=10, ttl_seconds=600
    )
    assert result is not None
    assert result.outcome is not None
    assert result.outcome.new_status == PaymentStatus.FAILED
