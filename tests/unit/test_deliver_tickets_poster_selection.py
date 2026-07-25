"""_deliver_tickets (channels/telegram/handlers.py, channels/vk/handlers.py)
выбирает один случайный постер из нескольких загруженных через веб-админку
(см. DECISIONS_LOG.md №46), а не единственный digital_poster_path, как раньше."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from app.core.db import Database
from app.models.giveaway import Giveaway
from app.models.giveaway_poster import GiveawayPoster
from app.services.payment_service import FinalizeOutcome


@dataclass
class FakeChannel:
    deliver_purchase_calls: list[dict] = field(default_factory=list)

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None:
        self.deliver_purchase_calls.append(
            {"external_user_id": external_user_id, "poster_path": poster_path}
        )


def make_giveaway_with_posters(db: Database, *, prefix: str, poster_paths: list[str]) -> int:
    with db.session() as session:
        giveaway = Giveaway(name="Test", prefix=prefix, ticket_price=10000, max_tickets=10)
        session.add(giveaway)
        session.flush()
        for path in poster_paths:
            session.add(GiveawayPoster(giveaway_id=giveaway.id, file_path=path))
        session.flush()
        return giveaway.id


async def test_telegram_deliver_tickets_picks_one_of_uploaded_posters(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channels.telegram import handlers

    gid = make_giveaway_with_posters(
        db, prefix="TGP", poster_paths=["/data/posters/1/a.png", "/data/posters/1/b.png"]
    )
    channel = FakeChannel()
    handlers.set_channel(channel)  # type: ignore[arg-type]
    monkeypatch.setattr(handlers, "get_channel_db", lambda: db)

    outcome = FinalizeOutcome(applied=True, giveaway_id=gid, tickets=[])
    message = SimpleNamespace(chat=SimpleNamespace(id=42))

    await handlers._deliver_tickets(message, outcome)  # type: ignore[arg-type]

    assert len(channel.deliver_purchase_calls) == 1
    poster_path = channel.deliver_purchase_calls[0]["poster_path"]
    assert poster_path in {"/data/posters/1/a.png", "/data/posters/1/b.png"}


async def test_telegram_deliver_tickets_none_when_no_posters(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channels.telegram import handlers

    gid = make_giveaway_with_posters(db, prefix="TGN", poster_paths=[])
    channel = FakeChannel()
    handlers.set_channel(channel)  # type: ignore[arg-type]
    monkeypatch.setattr(handlers, "get_channel_db", lambda: db)

    outcome = FinalizeOutcome(applied=True, giveaway_id=gid, tickets=[])
    message = SimpleNamespace(chat=SimpleNamespace(id=42))

    await handlers._deliver_tickets(message, outcome)  # type: ignore[arg-type]

    assert channel.deliver_purchase_calls[0]["poster_path"] is None


async def test_vk_deliver_tickets_picks_one_of_uploaded_posters(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channels.vk import handlers

    gid = make_giveaway_with_posters(
        db, prefix="VKP", poster_paths=["/data/posters/2/a.png", "/data/posters/2/b.png"]
    )
    channel = FakeChannel()
    handlers.set_channel(channel)  # type: ignore[arg-type]
    monkeypatch.setattr(handlers, "get_channel_db", lambda: db)

    outcome = FinalizeOutcome(applied=True, giveaway_id=gid, tickets=[])

    await handlers._deliver_tickets(4242, outcome)

    assert len(channel.deliver_purchase_calls) == 1
    poster_path = channel.deliver_purchase_calls[0]["poster_path"]
    assert poster_path in {"/data/posters/2/a.png", "/data/posters/2/b.png"}
