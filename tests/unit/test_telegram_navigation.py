"""Тесты навигации ("назад") в потоке покупки Telegram-бота
(channels/telegram/handlers.py) — см. DECISIONS.md. `_open_giveaways` — общая
для основного и "назад"-путей чистая функция, определяющая, показывать ли
пользователю список розыгрышей заново или сразу вести в главное меню; полный
прогон aiogram Dispatcher.feed_update в этом репозитории не используется (нет
прецедента мокания Bot API, см. tests/unit/test_telegram_handlers.py)."""

from __future__ import annotations

from app.core.db import Database
from app.models.giveaway import Giveaway
from app.services import ticket_pool_service as pool_svc
from sqlalchemy.orm import Session

from channels.telegram.handlers import _open_giveaways


def make_giveaway(
    db: Database, *, max_tickets: int = 10, prefix: str = "NAV", open_for_sale: bool = True
) -> int:
    with db.session() as session:
        g = Giveaway(name="Test", prefix=prefix, ticket_price=10000, max_tickets=max_tickets)
        session.add(g)
        session.flush()
        if open_for_sale:
            pool_svc.open_registration(session, g)
        return g.id


def test_open_giveaways_empty_when_none_open(session: Session) -> None:
    assert _open_giveaways(session) == []


def test_open_giveaways_returns_only_sellable_ones(db: Database) -> None:
    open_gid = make_giveaway(db, prefix="OPN")
    make_giveaway(db, prefix="CLS", open_for_sale=False)

    with db.session() as session:
        result = _open_giveaways(session)

    assert [g.id for g in result] == [open_gid]


def test_open_giveaways_excludes_sold_out_giveaway(db: Database) -> None:
    gid = make_giveaway(db, max_tickets=1, prefix="SLD")
    with db.session() as session:
        from app.models.participant import Participant

        p = Participant(phone="79990001122")
        session.add(p)
        session.flush()
        pid = p.id

    from app.payments.mock import MockProvider
    from app.services import payment_service as payment_svc

    outcome = payment_svc.create_payment_safe(
        db,
        MockProvider(),
        giveaway_id=gid,
        participant_id=pid,
        participant_phone="79990001122",
        quantity=1,
    )
    assert outcome.ok

    with db.session() as session:
        assert _open_giveaways(session) == []


def test_open_giveaways_reports_multiple_when_more_than_one_available(db: Database) -> None:
    gid_a = make_giveaway(db, prefix="AAA")
    gid_b = make_giveaway(db, prefix="BBB")

    with db.session() as session:
        result = {g.id for g in _open_giveaways(session)}

    assert result == {gid_a, gid_b}
