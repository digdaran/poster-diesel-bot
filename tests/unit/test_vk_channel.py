"""Тесты VkChannel (п.5.4.1, 10.6, 20.1 ТЗ): UI-примитивы, отправка сообщений
через мокнутый vkbottle API. VK активна в проде наравне с Telegram — см.
DECISIONS_LOG.md #32/#33."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from app.channels.factory import ACTIVE_CHANNELS, get_channel_class, is_channel_active
from app.models.enums import ChannelType

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


def test_render_payment_prompt_includes_pay_link_and_qr_button() -> None:
    channel = VkChannel(token="x")
    keyboard = json.loads(
        channel.render_payment_prompt(
            payment_url="https://pay.example/1", order_id="ORD1", has_qr=True
        )
    )
    actions = [btn["action"] for row in keyboard["buttons"] for btn in row]
    assert any(a.get("link") == "https://pay.example/1" for a in actions)
    assert any(a.get("payload") == {"a": "show_qr", "order_id": "ORD1"} for a in actions)
    assert any(a.get("payload") == {"a": "check_payment"} for a in actions)


def test_render_payment_prompt_omits_qr_button_when_unavailable() -> None:
    channel = VkChannel(token="x")
    keyboard = json.loads(
        channel.render_payment_prompt(
            payment_url="https://pay.example/1", order_id="ORD1", has_qr=False
        )
    )
    actions = [btn["action"] for row in keyboard["buttons"] for btn in row]
    assert not any(a.get("payload", {}).get("a") == "show_qr" for a in actions)
