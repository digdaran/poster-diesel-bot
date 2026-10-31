"""Кнопка «✅ Подтвердить» заказа (LOG №91): на проде повторное нажатие / старая
кнопка падали с `KeyError: 'giveaway_id'` и бот молчал, а быстрое двойное нажатие
могло создать два счёта — состояние очищалось только после отправки счёта."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from vkbottle.dispatch.dispenser.builtin import BuiltinStateDispenser

from channels.telegram import handlers as tg
from channels.vk import handlers as vk

_ORDER = {"giveaway_id": 7, "quantity": 2, "participant_id": 42}


def _slow_payment_mock() -> AsyncMock:
    """Создание счёта уступает управление (как реальная отправка сообщений),
    давая второму нажатию шанс вклиниться."""

    async def _create(*args: Any) -> None:
        await asyncio.sleep(0.01)

    return AsyncMock(side_effect=_create)


# --- Telegram ---------------------------------------------------------------


def _tg_state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=100, user_id=100))


def _tg_callback() -> Any:
    return SimpleNamespace(answer=AsyncMock(), message=object())


@pytest.fixture
def tg_payment(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = _slow_payment_mock()
    monkeypatch.setattr(tg, "_create_and_offer_payment", mock)
    monkeypatch.setattr(tg, "_msg", lambda callback: callback.message)
    return mock


async def test_tg_double_press_creates_single_payment(tg_payment: AsyncMock) -> None:
    state = _tg_state()
    await state.update_data(**_ORDER)
    first, second = _tg_callback(), _tg_callback()

    await asyncio.gather(
        tg.on_confirm_order_yes(first, state), tg.on_confirm_order_yes(second, state)
    )

    assert tg_payment.await_count == 1
    assert tg_payment.await_args.args[1:] == (7, 2, 42)
    second.answer.assert_awaited_once_with(tg._STALE_ORDER_TEXT, show_alert=True)
    assert await state.get_data() == {}


async def test_tg_stale_confirm_button_answers_instead_of_crashing(
    tg_payment: AsyncMock,
) -> None:
    callback = _tg_callback()
    await tg.on_confirm_order_yes(callback, _tg_state())
    tg_payment.assert_not_awaited()
    callback.answer.assert_awaited_once_with(tg._STALE_ORDER_TEXT, show_alert=True)


async def test_tg_stale_back_button_answers_instead_of_crashing() -> None:
    callback = _tg_callback()
    await tg.on_confirm_order_back(callback, _tg_state())
    callback.answer.assert_awaited_once_with(tg._STALE_ORDER_TEXT, show_alert=True)


# --- VK ---------------------------------------------------------------------


@pytest.fixture
def vk_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    dispenser = BuiltinStateDispenser()
    channel = SimpleNamespace(bot=SimpleNamespace(state_dispenser=dispenser))
    env = SimpleNamespace(dispenser=dispenser, payment=_slow_payment_mock(), answer=AsyncMock())
    monkeypatch.setattr(vk, "_get_channel", lambda: channel)
    monkeypatch.setattr(vk, "get_channel_db", lambda: None)
    monkeypatch.setattr(vk, "_create_and_offer_payment", env.payment)
    monkeypatch.setattr(vk, "_answer_event", env.answer)
    return env


def _vk_event(v: str) -> Any:
    return SimpleNamespace(
        object=SimpleNamespace(peer_id=200, payload={"a": "confirm_order", "v": v})
    )


async def test_vk_double_press_creates_single_payment(vk_env: SimpleNamespace) -> None:
    await vk._set_state(200, vk.PurchaseStates.CONFIRMING_ORDER, **_ORDER)

    await asyncio.gather(
        vk._dispatch_message_event(_vk_event("yes")),
        vk._dispatch_message_event(_vk_event("yes")),
    )

    assert vk_env.payment.await_count == 1
    assert vk_env.payment.await_args.args == (200, 7, 2, 42)
    snackbars = [c.kwargs.get("snackbar") for c in vk_env.answer.await_args_list]
    assert sorted(snackbars, key=str) == [None, vk._STALE_ORDER_TEXT]
    assert await vk_env.dispenser.get(200) is None


@pytest.mark.parametrize("v", ["yes", "back"])
async def test_vk_stale_confirm_buttons_answer_instead_of_crashing(
    vk_env: SimpleNamespace, v: str
) -> None:
    await vk._dispatch_message_event(_vk_event(v))
    vk_env.payment.assert_not_awaited()
    vk_env.answer.assert_awaited_once()
    assert vk_env.answer.await_args.kwargs["snackbar"] == vk._STALE_ORDER_TEXT
