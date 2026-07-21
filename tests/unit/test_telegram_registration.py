"""Тесты первичной привязки Telegram-аккаунта на /start (channels/telegram/handlers.py),
включая ручной ввод номера при включённом ignore_phone_verification (п.7.1 ТЗ) —
до этого исправления бот принимал номер на этом шаге ТОЛЬКО через нативный
Telegram-контакт («Поделиться контактом») и не имел текстового фолбэка."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from app.core.db import Database
from app.models.enums import ChannelType
from app.models.participant import Participant
from app.services import participant_service, settings_service

from channels.telegram import handlers as handlers_module
from channels.telegram.state import RegistrationStates


class _FakeUser:
    def __init__(self, uid: int, username: str | None = "tg_user") -> None:
        self.id = uid
        self.username = username


class _FakeMessage:
    def __init__(self, uid: int, text: str | None = None) -> None:
        self.from_user = _FakeUser(uid)
        self.text = text
        self.answer = AsyncMock()


class _FakeState:
    def __init__(self) -> None:
        self.clear = AsyncMock()
        self.set_state = AsyncMock()
        self.get_data = AsyncMock(return_value={})


class _FakeChannel:
    def __init__(self) -> None:
        self.request_contact = AsyncMock()
        self.render_keyboard = AsyncMock()


async def test_on_start_new_participant_with_ignore_verification_offers_text_phone(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    fake_channel = _FakeChannel()
    monkeypatch.setattr(handlers_module, "_channel", fake_channel)
    with db.session() as session:
        settings_service.get_or_create_settings(session).ignore_phone_verification = True

    message = _FakeMessage(uid=111)
    state = _FakeState()
    await handlers_module.on_start(message, state)  # type: ignore[arg-type]

    fake_channel.request_contact.assert_awaited_once_with("111")
    state.set_state.assert_awaited_once_with(RegistrationStates.awaiting_phone)


async def test_on_start_new_participant_without_ignore_verification_has_no_text_fallback(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    monkeypatch.setattr(handlers_module, "_channel", _FakeChannel())

    message = _FakeMessage(uid=112)
    state = _FakeState()
    await handlers_module.on_start(message, state)  # type: ignore[arg-type]

    state.set_state.assert_not_awaited()


async def test_on_phone_typed_for_registration_binds_participant_and_asks_name(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)

    message = _FakeMessage(uid=113, text="+7 999 000-11-22")
    state = _FakeState()
    await handlers_module.on_phone_typed_for_registration(message, state)  # type: ignore[arg-type]

    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id="113"
        )
        assert participant is not None
        assert participant.phone == "79990001122"
        assert participant.phone_verified is False

    state.set_state.assert_awaited_once_with(RegistrationStates.awaiting_name)
    message.answer.assert_awaited_with("Номер принят! Как вас зовут?")


async def test_on_phone_typed_for_registration_invalid_phone_stays_on_step(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)

    message = _FakeMessage(uid=114, text="not a phone")
    state = _FakeState()
    await handlers_module.on_phone_typed_for_registration(message, state)  # type: ignore[arg-type]

    state.set_state.assert_not_awaited()
    message.answer.assert_awaited_with("Не удалось распознать номер телефона. Попробуйте ещё раз.")


async def test_on_phone_typed_for_registration_conflict_with_existing_binding(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тот же телеграм-аккаунт уже привязан к другому номеру — повторная
    привязка запрещена (п.7.1 ТЗ), участник/привязка не меняются."""
    monkeypatch.setattr(handlers_module, "get_channel_db", lambda: db)
    with db.session() as session:
        p = Participant(phone="79990009999", phone_verified=False)
        session.add(p)
        session.flush()
        participant_service.bind_channel_ignoring_verification(
            session,
            channel=ChannelType.TELEGRAM,
            external_user_id="115",
            phone_raw="79990009999",
        )

    message = _FakeMessage(uid=115, text="79991112233")
    state = _FakeState()
    await handlers_module.on_phone_typed_for_registration(message, state)  # type: ignore[arg-type]

    state.set_state.assert_not_awaited()
    with db.session() as session:
        participant = participant_service.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id="115"
        )
        assert participant is not None
        assert participant.phone == "79990009999"
