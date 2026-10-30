"""Тексты акции «N + M в подарок» в ботах (DECISIONS_LOG.md №87): подписи кнопок количества,
пометка в списке постеров, акцент в диалоге, сообщение о выдаче с подарками и о подарках,
которые будут выданы вручную в следующем тираже."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.core.db import Database
from app.models.giveaway import Giveaway
from app.services import promo_texts
from app.services.payment_service import FinalizeOutcome


def promo_giveaway(
    *, buy_n: int | None = 5, gift_m: int | None = 1, max_tickets: int = 100, issued: int = 0
) -> Giveaway:
    return Giveaway(
        id=1,
        name="Постер",
        prefix="PRM",
        ticket_price=10000,
        max_tickets=max_tickets,
        tickets_issued=issued,
        tickets_reserved=0,
        promo_buy_n=buy_n,
        promo_gift_m=gift_m,
    )


def test_quantity_labels_mark_gift_options() -> None:
    g = promo_giveaway()
    assert [promo_texts.quantity_label(g, q) for q in (1, 3, 5, 10)] == ["1", "3", "5+1", "10+2"]


def test_quantity_labels_without_promo_are_plain() -> None:
    g = promo_giveaway(buy_n=None, gift_m=None)
    assert [promo_texts.quantity_label(g, q) for q in (1, 3, 5, 10)] == ["1", "3", "5", "10"]


def test_badge_and_promo_line() -> None:
    g = promo_giveaway(buy_n=5, gift_m=2)
    assert promo_texts.giveaway_badge(g) == " 🎁 5+2"
    assert "за каждые 5 экз." in promo_texts.promo_line(g)
    assert "2 экз. этого же постера в подарок" in promo_texts.promo_line(g)
    plain = promo_giveaway(buy_n=None, gift_m=None)
    assert promo_texts.giveaway_badge(plain) == ""
    assert promo_texts.promo_line(plain) == ""
    assert promo_texts.order_gift_line(plain, 5) == ""


def test_order_gift_line_shows_gift_and_total() -> None:
    line = promo_texts.order_gift_line(promo_giveaway(), 10)
    assert "+ 2 экз. в подарок" in line
    assert "всего получите 12 экз." in line
    assert "Добавьте" not in line


def test_order_gift_line_suggests_reaching_next_threshold() -> None:
    line = promo_texts.order_gift_line(promo_giveaway(), 4)
    assert "Добавьте ещё 1 экз. — и получите 1 экз. в подарок" in line
    line = promo_texts.order_gift_line(promo_giveaway(), 7)
    assert "+ 1 экз. в подарок" in line
    assert "Добавьте ещё 3 экз. — и получите ещё 1 экз. в подарок" in line


def test_order_gift_line_no_suggestion_when_not_enough_left() -> None:
    line = promo_texts.order_gift_line(promo_giveaway(max_tickets=10, issued=6), 4)
    assert "Добавьте" not in line


def test_delivery_intro_unchanged_without_gifts() -> None:
    base = "✅ Оплата прошла успешно! Ваши постеры куплены, номера:"
    assert promo_texts.delivery_intro(base, gifts_issued=0, gifts_not_issued=0) == base


def test_delivery_intro_and_codes_with_gifts_and_shortfall() -> None:
    intro = promo_texts.delivery_intro(
        "✅ Оплата прошла успешно! Ваши постеры куплены, номера:",
        gifts_issued=1,
        gifts_not_issued=1,
    )
    assert intro.startswith("✅ Оплата прошла успешно! Ваши постеры куплены.")
    assert "Плюс 1 экз. в подарок" in intro
    assert "будут выданы вручную в следующем тираже" in intro
    assert intro.endswith("Номера:")
    assert promo_texts.delivery_codes(["A-1", "A-2"], ["A-3"]) == ["A-1", "A-2", "A-3 🎁"]


def _payload(button: dict[str, Any]) -> dict[str, Any]:
    raw = button["action"]["payload"]
    return json.loads(raw) if isinstance(raw, str) else raw  # type: ignore[no-any-return]


@dataclass
class FakeChannel:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None:
        self.calls.append({"codes": codes, "intro": intro})


def _save_giveaway(db: Database, **kwargs: Any) -> int:
    with db.session() as session:
        g = Giveaway(name="Постер", prefix="TGG", ticket_price=10000, max_tickets=20, **kwargs)
        session.add(g)
        session.flush()
        return g.id


async def test_telegram_deliver_tickets_marks_gifts_and_shortfall(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channels.telegram import handlers

    gid = _save_giveaway(db, promo_buy_n=5, promo_gift_m=1)
    channel = FakeChannel()
    handlers.set_channel(channel)  # type: ignore[arg-type]
    monkeypatch.setattr(handlers, "get_channel_db", lambda: db)
    outcome = FinalizeOutcome(
        applied=True,
        giveaway_id=gid,
        tickets=[SimpleNamespace(full_code="TGG-000001")],  # type: ignore[list-item]
        gift_tickets=[SimpleNamespace(full_code="TGG-000002")],  # type: ignore[list-item]
        gifts_not_issued=1,
    )

    await handlers._deliver_tickets(SimpleNamespace(chat=SimpleNamespace(id=1)), outcome)  # type: ignore[arg-type]

    call = channel.calls[0]
    assert call["codes"] == ["TGG-000001", "TGG-000002 🎁"]
    assert "в следующем тираже" in call["intro"]


async def test_telegram_quantity_prompt_shows_gift_buttons_and_promo(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channels.telegram import handlers

    gid = _save_giveaway(db, promo_buy_n=5, promo_gift_m=1, is_registration_open=True)
    monkeypatch.setattr(handlers, "get_channel_db", lambda: db)
    with db.session() as session:
        giveaway = session.get(Giveaway, gid)
    message = SimpleNamespace(answer=AsyncMock())
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())

    await handlers._prompt_quantity(message, state, giveaway)  # type: ignore[arg-type]

    text = message.answer.await_args.args[0]
    markup = message.answer.await_args.kwargs["reply_markup"]
    labels = [b.text for b in markup.inline_keyboard[0]]
    callbacks = [b.callback_data for b in markup.inline_keyboard[0]]
    assert labels == ["1", "3", "5+1", "10+2"]
    assert callbacks == [f"qty:{gid}:{q}" for q in (1, 3, 5, 10)]
    assert "🎁 Акция" in text


async def test_vk_quantity_prompt_shows_gift_buttons(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channels.vk import handlers

    gid = _save_giveaway(db, promo_buy_n=5, promo_gift_m=1, is_registration_open=True)
    monkeypatch.setattr(handlers, "get_channel_db", lambda: db)
    monkeypatch.setattr(handlers, "_set_state", AsyncMock())
    with db.session() as session:
        giveaway = session.get(Giveaway, gid)
    message = SimpleNamespace(answer=AsyncMock())

    await handlers._prompt_quantity(1, giveaway, answer_target=message)  # type: ignore[arg-type]

    text = message.answer.await_args.args[0]
    buttons = json.loads(message.answer.await_args.kwargs["keyboard"])["buttons"]
    labels = [b["action"]["label"] for b in buttons[0]]
    payloads = [_payload(b)["q"] for b in buttons[0]]
    assert labels == ["1", "3", "5+1", "10+2"]
    assert payloads == [1, 3, 5, 10]
    assert "🎁 Акция" in text


@pytest.mark.parametrize(
    "name", ["Короткое", "Очень длинное название коллекции постеров 2026 года"]
)
def test_vk_giveaway_label_fits_40_chars(name: str) -> None:
    from channels.vk import handlers

    label = handlers._fit_vk_label(name, " 🎁 10+2 (99999 своб.)")
    assert len(label.encode("utf-16-le")) // 2 <= 40, label
    assert label.endswith(" 🎁 10+2 (99999 своб.)")
