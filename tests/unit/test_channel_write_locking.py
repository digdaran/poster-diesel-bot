"""Пишущие обработчики ботов не падают с `database is locked`, если другой
процесс коммитит запись между их чтением и записью (прод 2026-10-31, VK
`on_phone_typed_for_registration`; DECISIONS_LOG.md №92). Плюс отказ на шаге
покупки, если номер уже привязан к другому аккаунту канала (LOG №90, №92)."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from app.core.db import Database
from app.models.audit_log import AuditLog
from app.models.enums import AuditActorType, ChannelType
from app.services import participant_service as svc
from app.services import settings_service
from sqlalchemy.exc import OperationalError
from vkbottle.dispatch.dispenser.builtin import BuiltinStateDispenser

from channels.telegram import handlers as tg
from channels.vk import handlers as vk


def _concurrent_write(db: Database) -> threading.Thread:
    """Другой писатель (как фоновая сверка банка) пытается закоммитить запись."""
    errors: list[Exception] = []

    def write() -> None:
        try:
            with db.session() as other:
                other.add(
                    AuditLog(
                        action="concurrent_write",
                        actor_type=AuditActorType.SYSTEM,
                        actor_label="test",
                    )
                )
        except Exception as exc:  # pragma: no cover - проверяется ниже
            errors.append(exc)

    thread = threading.Thread(target=write)
    thread.errors = errors  # type: ignore[attr-defined]
    thread.start()
    thread.join(timeout=0.3)  # даём писателю попытаться, пока мы посреди транзакции
    return thread


@pytest.fixture
def write_between_read_and_write(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> list[threading.Thread]:
    """Вклинивает чужую запись сразу после поиска участника по номеру — между
    чтением и INSERT в `bind_channel_ignoring_verification`."""
    threads: list[threading.Thread] = []
    real_find = svc._find_participant_by_phone

    def find_then_concurrent_write(session: Any, phone: str) -> Any:
        found = real_find(session, phone)
        threads.append(_concurrent_write(db))
        return found

    monkeypatch.setattr(svc, "_find_participant_by_phone", find_then_concurrent_write)
    return threads


def test_deferred_session_reproduces_database_is_locked(
    db: Database, write_between_read_and_write: list[threading.Thread]
) -> None:
    """Контроль: в обычной deferred-сессии та же гонка даёт мгновенный
    `database is locked` (SQLITE_BUSY_SNAPSHOT) — так было на проде."""
    with (
        pytest.raises(OperationalError, match="database is locked"),
        db.session() as session,
    ):
        svc.bind_channel_ignoring_verification(
            session, channel=ChannelType.VK, external_user_id="vk-1", phone_raw="79990009101"
        )


class _FakeVkMessage:
    def __init__(self, peer_id: int, text: str) -> None:
        self.peer_id = peer_id
        self.text = text
        self.answer = AsyncMock()


@pytest.fixture
def vk_env(db: Database, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    dispenser = BuiltinStateDispenser()
    channel = SimpleNamespace(
        bot=SimpleNamespace(state_dispenser=dispenser), render_keyboard=lambda buttons: "{}"
    )
    monkeypatch.setattr(vk, "_get_channel", lambda: channel)
    monkeypatch.setattr(vk, "get_channel_db", lambda: db)
    return SimpleNamespace(dispenser=dispenser)


async def test_vk_registration_survives_concurrent_write(
    db: Database,
    vk_env: SimpleNamespace,
    write_between_read_and_write: list[threading.Thread],
) -> None:
    message = _FakeVkMessage(peer_id=301, text="79990009102")

    await vk.on_phone_typed_for_registration(message)  # type: ignore[arg-type]

    for thread in write_between_read_and_write:
        thread.join(timeout=10)
        assert not thread.errors  # type: ignore[attr-defined]
    message.answer.assert_awaited_once_with("Номер принят ✅! Как вас зовут? 🙂")
    with db.session() as session:
        assert svc.get_binding(session, channel=ChannelType.VK, external_user_id="301")


def _enable_ignore_phone_verification(db: Database) -> None:
    with db.session() as session:
        settings_service.get_or_create_settings(session).ignore_phone_verification = True


async def test_vk_purchase_phone_bound_to_other_account_is_refused(
    db: Database, vk_env: SimpleNamespace
) -> None:
    _enable_ignore_phone_verification(db)
    with db.session() as session:
        svc.bind_channel_ignoring_verification(
            session, channel=ChannelType.VK, external_user_id="vk-owner", phone_raw="79990009103"
        )
    await vk._set_state(302, vk.PurchaseStates.AWAITING_PHONE_FOR_GIFT, giveaway_id=1, quantity=1)
    message = _FakeVkMessage(peer_id=302, text="79990009103")

    await vk.on_phone_entered(message)  # type: ignore[arg-type]

    message.answer.assert_awaited_once_with(vk._PHONE_BOUND_TEXT)
    peer = await vk_env.dispenser.get(302)
    assert peer is not None and peer.payload["giveaway_id"] == 1  # шаг покупки сохранён


async def test_tg_purchase_phone_bound_to_other_account_is_refused(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tg, "get_channel_db", lambda: db)
    _enable_ignore_phone_verification(db)
    with db.session() as session:
        svc.bind_channel_ignoring_verification(
            session,
            channel=ChannelType.TELEGRAM,
            external_user_id="tg-owner",
            phone_raw="79990009104",
        )
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=5, user_id=5))
    await state.update_data(giveaway_id=1, quantity=1)
    message = SimpleNamespace(
        text="79990009104", answer=AsyncMock(), from_user=None, chat=SimpleNamespace(id=5)
    )
    monkeypatch.setattr(tg, "_uid", lambda m: "5")

    await tg.on_phone_entered(message, state)  # type: ignore[arg-type]

    message.answer.assert_awaited_once_with(tg._PHONE_BOUND_TEXT)
    assert (await state.get_data())["giveaway_id"] == 1
