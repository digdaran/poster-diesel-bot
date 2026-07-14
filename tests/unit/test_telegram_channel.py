"""Тесты TelegramChannel и фабрики каналов (п.5.4.1, 10.4, 20.1 ТЗ):
UI-примитивы, отправка сообщений/медиа/QR через мокнутый aiogram Bot,
заглушки VK/MAX поднимают NotImplementedError, активен только Telegram."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from app.channels.factory import ACTIVE_CHANNELS, get_channel_class, is_channel_active
from app.models.enums import ChannelType

from channels.telegram.channel import TelegramChannel


@pytest.fixture
def channel() -> TelegramChannel:
    ch = TelegramChannel(token="123456:test-token-not-real")
    ch.bot.send_message = AsyncMock()  # type: ignore[method-assign]
    ch.bot.send_photo = AsyncMock()  # type: ignore[method-assign]
    return ch


async def test_send_message_delegates_to_bot(channel: TelegramChannel) -> None:
    await channel.send_message("123", "привет")
    channel.bot.send_message.assert_awaited_once_with(chat_id="123", text="привет")


async def test_send_media_raises_if_file_missing(channel: TelegramChannel) -> None:
    with pytest.raises(FileNotFoundError):
        await channel.send_media("123", "/no/such/file.png")


async def test_send_media_sends_photo(channel: TelegramChannel, tmp_path) -> None:  # noqa: ANN001
    poster = tmp_path / "poster.png"
    poster.write_bytes(b"\x89PNG\r\n\x1a\n")
    await channel.send_media("123", str(poster), caption="Ваш постер")
    channel.bot.send_photo.assert_awaited_once()
    _, kwargs = channel.bot.send_photo.call_args
    assert kwargs["chat_id"] == "123"
    assert kwargs["caption"] == "Ваш постер"


async def test_send_qr_code_generates_png(channel: TelegramChannel) -> None:
    await channel.send_qr_code("123", "mock-sbp-qr:order-1:1000")
    channel.bot.send_photo.assert_awaited_once()
    _, kwargs = channel.bot.send_photo.call_args
    assert kwargs["chat_id"] == "123"
    photo = kwargs["photo"]
    assert photo.data[:8] == b"\x89PNG\r\n\x1a\n"  # валидный PNG-заголовок


def test_render_keyboard_builds_reply_markup(channel: TelegramChannel) -> None:
    markup = channel.render_keyboard([["Купить", "Мои номерки"], ["Помощь"]])
    assert len(markup.keyboard) == 2
    assert markup.keyboard[0][0].text == "Купить"


def test_render_payment_prompt_has_pay_and_check_buttons(channel: TelegramChannel) -> None:
    markup = channel.render_payment_prompt(
        payment_url="https://pay.example/1", order_id="order-1", has_qr=False
    )
    flat = [btn for row in markup.inline_keyboard for btn in row]
    assert any(btn.url == "https://pay.example/1" for btn in flat)
    assert any(btn.callback_data == "check_payment" for btn in flat)
    assert not any((btn.callback_data or "").startswith("show_qr:") for btn in flat)
    assert len(markup.inline_keyboard) == 2


def test_render_payment_prompt_adds_qr_button_when_available(channel: TelegramChannel) -> None:
    markup = channel.render_payment_prompt(
        payment_url="https://pay.example/1", order_id="order-1", has_qr=True
    )
    flat = [btn for row in markup.inline_keyboard for btn in row]
    assert any(btn.callback_data == "show_qr:order-1" for btn in flat)
    assert len(markup.inline_keyboard) == 3


def test_only_telegram_active_in_production() -> None:
    assert frozenset({ChannelType.TELEGRAM}) == ACTIVE_CHANNELS
    assert is_channel_active(ChannelType.TELEGRAM) is True
    assert is_channel_active(ChannelType.VK) is False
    assert is_channel_active(ChannelType.MAX) is False


async def test_vk_channel_stub_raises_not_implemented() -> None:
    vk_cls = get_channel_class(ChannelType.VK)
    vk = vk_cls()
    with pytest.raises(NotImplementedError):
        await vk.send_message("1", "hi")
    with pytest.raises(NotImplementedError):
        await vk.request_contact("1")


async def test_max_channel_stub_raises_not_implemented() -> None:
    max_cls = get_channel_class(ChannelType.MAX)
    max_channel = max_cls()
    with pytest.raises(NotImplementedError):
        await max_channel.send_media("1", "/tmp/x.png")
