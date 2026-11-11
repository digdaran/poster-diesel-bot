"""Тесты VkChannel (п.5.4.1, 10.6, 20.1 ТЗ): UI-примитивы, отправка сообщений
через мокнутый vkbottle API. VK активна в проде наравне с Telegram — см.
DECISIONS_LOG.md #32/#33."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from app.channels.base import RecipientGoneError
from app.channels.factory import ACTIVE_CHANNELS, get_channel_class, is_channel_active
from app.models.enums import ChannelType
from vkbottle import VKAPIError

from channels.vk.channel import VkChannel


@pytest.fixture
def channel() -> VkChannel:
    ch = VkChannel(token="test-group-token", group_id=1)
    ch.bot.api.request = AsyncMock(return_value={"response": 1})  # type: ignore[method-assign]
    return ch


async def test_send_message_delegates_to_api(channel: VkChannel) -> None:
    await channel.send_message("123", "привет")
    channel.bot.api.request.assert_awaited_once()
    method, data = channel.bot.api.request.call_args.args[:2]
    assert method == "messages.send"
    assert data["peer_id"] == 123
    assert data["message"] == "привет"


async def test_send_media_raises_if_file_missing(channel: VkChannel) -> None:
    with pytest.raises(FileNotFoundError):
        await channel.send_media("123", "/no/such/file.png")


async def test_request_contact_explains_impossibility(channel: VkChannel) -> None:
    """У VK нет аналога Telegram `request_contact` (`supports_verified_phone=False`,
    см. ARCHITECTURE.md §7.1) — метод обязан явно сообщить об этом, а не молчать."""
    await channel.request_contact("123")
    channel.bot.api.request.assert_awaited_once()
    _, data = channel.bot.api.request.call_args.args[:2]
    assert "номер телефона" in data["message"]


async def test_send_qr_code_uploads_photo_and_sends_with_caption(channel: VkChannel) -> None:
    """Покрывает upload-флоу `send_qr_code` (getMessagesUploadServer ->
    загрузка -> saveMessagesPhoto -> messages.send), ранее не тестировавшийся
    отдельно от Telegram (см. tests/unit/test_telegram_channel.py)."""

    async def fake_request(method: str, params: dict | None = None) -> dict:
        if method == "photos.getMessagesUploadServer":
            return {"response": {"upload_url": "https://upload.vk.example/upload"}}
        if method == "photos.saveMessagesPhoto":
            return {"response": [{"owner_id": 1, "id": 2, "access_key": "abc"}]}
        if method == "messages.send":
            return {"response": 1}
        raise AssertionError(f"unexpected VK API method: {method}")

    channel.bot.api.request = AsyncMock(side_effect=fake_request)  # type: ignore[method-assign]
    channel.bot.api.http_client.request_text = AsyncMock(  # type: ignore[method-assign]
        return_value=json.dumps({"file": "upload-token"})
    )

    await channel.send_qr_code("123", "mock-sbp-qr:order-1:1000", caption="Инструкция по оплате")

    calls = {call.args[0]: call.args[1] for call in channel.bot.api.request.call_args_list}
    assert calls["photos.saveMessagesPhoto"]["file"] == "upload-token"
    assert calls["messages.send"]["peer_id"] == 123
    assert calls["messages.send"]["message"] == "Инструкция по оплате"
    assert calls["messages.send"]["attachment"] == "photo1_2_abc"


def test_vk_active_in_prod() -> None:
    """Включено по прямому запросу заказчика (DECISIONS_LOG.md #33), после того как
    адаптер реализован и покрыт тестами (DECISIONS_LOG.md #32)."""
    assert is_channel_active(ChannelType.VK)
    assert ChannelType.VK in ACTIVE_CHANNELS
    assert get_channel_class(ChannelType.VK) is VkChannel


def test_capabilities_reflect_vk_limitations() -> None:
    caps = VkChannel.capabilities
    assert caps.supports_verified_phone is False
    assert caps.can_initiate_dialog is True
    assert caps.supports_inline_buttons is True
    assert caps.supports_qr is True
    assert caps.media_send_mode == "upload"


def test_render_keyboard_produces_valid_vk_json() -> None:
    channel = VkChannel(token="x")
    keyboard = json.loads(channel.render_keyboard([["A", "B"], ["C"]]))
    assert keyboard["inline"] is False
    assert [btn["action"]["label"] for btn in keyboard["buttons"][0]] == ["A", "B"]
    assert [btn["action"]["label"] for btn in keyboard["buttons"][1]] == ["C"]


def test_render_payment_prompt_includes_pay_link_when_available() -> None:
    channel = VkChannel(token="x")
    keyboard = json.loads(channel.render_payment_prompt(payment_url="https://pay.example/1"))
    actions = [btn["action"] for row in keyboard["buttons"] for btn in row]
    assert any(a.get("link") == "https://pay.example/1" for a in actions)


def test_render_payment_prompt_returns_none_without_pay_link() -> None:
    channel = VkChannel(token="x")
    assert channel.render_payment_prompt(payment_url=None) is None


def test_render_support_prompt_has_open_link_button() -> None:
    channel = VkChannel(token="x")
    keyboard = json.loads(channel.render_support_prompt(url="https://vk.com/support_group"))
    actions = [btn["action"] for row in keyboard["buttons"] for btn in row]
    assert any(a.get("link") == "https://vk.com/support_group" for a in actions)


def _api_failing_send(send_error: Exception, users_response: object) -> tuple[AsyncMock, list[str]]:
    called: list[str] = []

    async def fake_request(method: str, params: dict | None = None) -> object:
        called.append(method)
        if method == "messages.send":
            raise send_error
        if method == "users.get":
            if isinstance(users_response, Exception):
                raise users_response
            return users_response
        raise AssertionError(f"unexpected VK API method: {method}")

    return AsyncMock(side_effect=fake_request), called


def _user(**extra: object) -> dict:
    return {"response": [{"id": 876540389, "first_name": "DELETED", "last_name": "", **extra}]}


async def test_send_to_deleted_account_raises_recipient_gone(channel: VkChannel) -> None:
    """Удалённому аккаунту VK отвечает общим `Access denied` — канал сам уточняет
    через users.get и сообщает очереди, что повторять бессмысленно (LOG №95)."""
    request, called = _api_failing_send(
        VKAPIError[15](error_msg="Access denied"), _user(deactivated="deleted")
    )
    channel.bot.api.request = request  # type: ignore[method-assign]

    with pytest.raises(RecipientGoneError, match="deleted"):
        await channel.send_message("876540389", "привет")
    assert called == ["messages.send", "users.get"]


async def test_access_denied_for_live_account_stays_transient(channel: VkChannel) -> None:
    request, _ = _api_failing_send(VKAPIError[15](error_msg="Access denied"), _user())
    channel.bot.api.request = request  # type: ignore[method-assign]

    with pytest.raises(VKAPIError):
        await channel.send_message("876540389", "привет")


async def test_users_get_failure_keeps_original_error(channel: VkChannel) -> None:
    request, _ = _api_failing_send(
        VKAPIError[15](error_msg="Access denied"), VKAPIError[10](error_msg="Internal")
    )
    channel.bot.api.request = request  # type: ignore[method-assign]

    with pytest.raises(VKAPIError) as info:
        await channel.send_message("876540389", "привет")
    assert getattr(info.value, "code", None) == 15


async def test_known_permanent_code_skips_users_get(channel: VkChannel) -> None:
    request, called = _api_failing_send(
        VKAPIError[901](error_msg="Can't send messages"), _user(deactivated="deleted")
    )
    channel.bot.api.request = request  # type: ignore[method-assign]

    with pytest.raises(VKAPIError):
        await channel.send_message("876540389", "привет")
    assert called == ["messages.send"]
