"""Тесты пула номеров и резервирования (п.7.5, 20.1 ТЗ):
атомарный захват, "всё-или-ничего", возврат в оборот, выдача, конкурентный захват
на хвосте тиража, остановка/возобновление продаж."""

from __future__ import annotations

import threading

import pytest
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import PaymentProviderType, PaymentStatus, TicketPoolStatus
from app.models.giveaway import Giveaway
from app.models.participant import Participant
from app.models.payment import Payment
from app.repositories import ticket_pool_repo as repo
from app.services import ticket_pool_service as svc
from app.services.ticket_pool_service import GiveawayNotSellableError
from sqlalchemy import select


def make_giveaway(db: Database, *, max_tickets: int = 10, prefix: str = "AUG") -> int:
    with db.session() as session:
        g = Giveaway(name="Test", prefix=prefix, ticket_price=10000, max_tickets=max_tickets)
        session.add(g)
        session.flush()
        svc.open_registration(session, g)
        return g.id


def make_participant(db: Database, phone: str = "79991234567") -> int:
    with db.session() as session:
        p = Participant(phone=phone)
        session.add(p)
        session.flush()
        return p.id


def make_payment(
    db: Database, *, giveaway_id: int, participant_id: int, order_id: str, quantity: int = 1
) -> int:
    with db.session() as session:
        payment = Payment(
            order_id=order_id,
            participant_id=participant_id,
            giveaway_id=giveaway_id,
            provider=PaymentProviderType.MOCK,
            amount=quantity * 10000,
            quantity=quantity,
            status=PaymentStatus.PENDING,
        )
        session.add(payment)
        session.flush()
        return payment.id


def test_open_registration_materializes_full_pool(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=25)
    with db.session() as session:
        count = len(
            list(
                session.execute(
                    select(repo.TicketPool).where(repo.TicketPool.giveaway_id == gid)
                ).scalars()
            )
        )
        assert count == 25
        statuses = {row.status for row in session.execute(select(repo.TicketPool)).scalars()}
        assert statuses == {TicketPoolStatus.FREE}


def test_open_registration_twice_forbidden(db: Database) -> None:
    with db.session() as session:
        g = Giveaway(name="Test", prefix="X1", ticket_price=100, max_tickets=5)
        session.add(g)
        session.flush()
        svc.open_registration(session, g)
    with db.session() as session:
        g = session.execute(select(Giveaway).where(Giveaway.prefix == "X1")).scalar_one()
        with pytest.raises(ValueError):
            svc.open_registration(session, g)


def test_reserve_all_or_nothing_success(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    payment_id = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o1", quantity=5)
    outcome = svc.reserve_for_payment(
        db, giveaway_id=gid, quantity=5, participant_id=pid, payment_id=payment_id, ttl_seconds=600
    )
    assert outcome.ok
    assert len(outcome.reserved) == 5
    assert svc.get_free_count(db, giveaway_id=gid) == 5


def test_reserve_all_or_nothing_insufficient(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=3)
    pid = make_participant(db)
    payment_id = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o2", quantity=5)
    outcome = svc.reserve_for_payment(
        db, giveaway_id=gid, quantity=5, participant_id=pid, payment_id=payment_id, ttl_seconds=600
    )
    assert not outcome.ok
    assert outcome.reserved == []
    assert outcome.free_count == 3  # актуальный остаток возвращён, резерв не создан
    # Пул не тронут — все ещё free
    assert svc.get_free_count(db, giveaway_id=gid) == 3


def test_release_reservation_returns_to_free(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    payment_id = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o3", quantity=4)
    outcome = svc.reserve_for_payment(
        db, giveaway_id=gid, quantity=4, participant_id=pid, payment_id=payment_id, ttl_seconds=600
    )
    assert outcome.ok
    assert svc.get_free_count(db, giveaway_id=gid) == 6
    released = svc.release_payment_reservation(db, payment_id=payment_id)
    assert released == 4
    assert svc.get_free_count(db, giveaway_id=gid) == 10
    # Повторное освобождение — no-op
    assert svc.release_payment_reservation(db, payment_id=payment_id) == 0


def test_issue_reserved_transitions_to_issued(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=10)
    pid = make_participant(db)
    payment_id = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o4", quantity=3)
    outcome = svc.reserve_for_payment(
        db, giveaway_id=gid, quantity=3, participant_id=pid, payment_id=payment_id, ttl_seconds=600
    )
    assert outcome.ok
    with db.immediate_session() as session:
        issued = repo.issue_reserved(session, payment_id=payment_id, issued_at=utcnow())
    assert len(issued) == 3
    assert all(row.status == TicketPoolStatus.ISSUED for row in issued)
    with db.session() as session:
        g = session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()
        assert g.tickets_issued == 3
        assert g.tickets_reserved == 0


def test_sale_stops_when_pool_exhausted_and_resumes_after_release(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=5)
    pid = make_participant(db)
    payment_id1 = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o5", quantity=5)
    outcome1 = svc.reserve_for_payment(
        db, giveaway_id=gid, quantity=5, participant_id=pid, payment_id=payment_id1, ttl_seconds=600
    )
    assert outcome1.ok
    assert svc.get_free_count(db, giveaway_id=gid) == 0

    payment_id2 = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o6", quantity=1)
    outcome2 = svc.reserve_for_payment(
        db, giveaway_id=gid, quantity=1, participant_id=pid, payment_id=payment_id2, ttl_seconds=600
    )
    assert not outcome2.ok
    assert outcome2.free_count == 0

    svc.release_payment_reservation(db, payment_id=payment_id1)
    assert svc.get_free_count(db, giveaway_id=gid) == 5

    payment_id3 = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o7", quantity=5)
    outcome3 = svc.reserve_for_payment(
        db, giveaway_id=gid, quantity=5, participant_id=pid, payment_id=payment_id3, ttl_seconds=600
    )
    assert outcome3.ok


def test_locked_giveaway_rejects_reservation(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=5)
    pid = make_participant(db)
    with db.session() as session:
        g = session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()
        g.is_locked = True
    payment_id = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o8", quantity=1)
    with pytest.raises(GiveawayNotSellableError):
        svc.reserve_for_payment(
            db,
            giveaway_id=gid,
            quantity=1,
            participant_id=pid,
            payment_id=payment_id,
            ttl_seconds=600,
        )


def test_closed_registration_rejects_reservation(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=5)
    pid = make_participant(db)
    with db.session() as session:
        g = session.execute(select(Giveaway).where(Giveaway.id == gid)).scalar_one()
        g.is_registration_open = False
    payment_id = make_payment(db, giveaway_id=gid, participant_id=pid, order_id="o9", quantity=1)
    with pytest.raises(GiveawayNotSellableError):
        svc.reserve_for_payment(
            db,
            giveaway_id=gid,
            quantity=1,
            participant_id=pid,
            payment_id=payment_id,
            ttl_seconds=600,
        )


def test_concurrent_capture_on_the_tail_no_duplicates_no_overselling(db: Database) -> None:
    """Сценарий из п.7.5 ТЗ: свободно 10, двое одновременно запрашивают по 10 —
    первый захватывает всё, второй сериализуется и получает остаток 0."""
    gid = make_giveaway(db, max_tickets=10)
    pid_a = make_participant(db, "79990000001")
    pid_b = make_participant(db, "79990000002")

    outcomes: dict[str, svc.ReservationOutcome] = {}
    barrier = threading.Barrier(2)

    payment_id_a = make_payment(
        db, giveaway_id=gid, participant_id=pid_a, order_id="tail-a", quantity=10
    )
    payment_id_b = make_payment(
        db, giveaway_id=gid, participant_id=pid_b, order_id="tail-b", quantity=10
    )

    def worker(name: str, pid: int, payment_id: int) -> None:
        barrier.wait()
        outcomes[name] = svc.reserve_for_payment(
            db,
            giveaway_id=gid,
            quantity=10,
            participant_id=pid,
            payment_id=payment_id,
            ttl_seconds=600,
        )

    t1 = threading.Thread(target=worker, args=("A", pid_a, payment_id_a))
    t2 = threading.Thread(target=worker, args=("B", pid_b, payment_id_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    successes = [o for o in outcomes.values() if o.ok]
    failures = [o for o in outcomes.values() if not o.ok]
    assert len(successes) == 1, "ровно один из двух должен выиграть захват всех 10 номеров"
    assert len(failures) == 1
    assert failures[0].free_count == 0
    assert svc.get_free_count(db, giveaway_id=gid) == 0

    # Проверка отсутствия дублей/превышения тиража на уровне БД
    with db.session() as session:
        reserved_ids = [
            row.id
            for row in session.execute(
                select(repo.TicketPool).where(
                    repo.TicketPool.giveaway_id == gid,
                    repo.TicketPool.status == TicketPoolStatus.RESERVED,
                )
            ).scalars()
        ]
        assert len(reserved_ids) == len(set(reserved_ids)) == 10


def test_concurrent_capture_partial_tail_winner_and_actual_remainder(db: Database) -> None:
    """Свободно 10; A просит 7, B просит 7 одновременно — ровно один должен получить
    отказ с точным актуальным остатком (0 после первого успеха)."""
    gid = make_giveaway(db, max_tickets=10)
    pid_a = make_participant(db, "79990000003")
    pid_b = make_participant(db, "79990000004")

    outcomes: dict[str, svc.ReservationOutcome] = {}
    barrier = threading.Barrier(2)

    payment_id_a = make_payment(
        db, giveaway_id=gid, participant_id=pid_a, order_id="partial-a", quantity=7
    )
    payment_id_b = make_payment(
        db, giveaway_id=gid, participant_id=pid_b, order_id="partial-b", quantity=7
    )

    def worker(name: str, pid: int, payment_id: int) -> None:
        barrier.wait()
        outcomes[name] = svc.reserve_for_payment(
            db,
            giveaway_id=gid,
            quantity=7,
            participant_id=pid,
            payment_id=payment_id,
            ttl_seconds=600,
        )

    threads = [
        threading.Thread(target=worker, args=("A", pid_a, payment_id_a)),
        threading.Thread(target=worker, args=("B", pid_b, payment_id_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    successes = [o for o in outcomes.values() if o.ok]
    failures = [o for o in outcomes.values() if not o.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].free_count == 3  # 10 - 7 захваченных первым
