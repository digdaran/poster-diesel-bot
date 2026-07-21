"""Тесты TelegramChannel и фабрики каналов (п.5.4.1, 10.4, 20.1 ТЗ):
UI-примитивы, отправка сообщений/медиа/QR через мокнутый aiogram Bot,
заглушка MAX поднимает NotImplementedError. Telegram и VK активны в проде
(см. tests/unit/test_vk_channel.py, DECISIONS.md #32/#33)."""

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
    assert any(btn.callback_data == "cancel_payment:order-1" for btn in flat)
    assert not any((btn.callback_data or "").startswith("show_qr:") for btn in flat)
    assert len(markup.inline_keyboard) == 3


def test_render_payment_prompt_adds_qr_button_when_available(channel: TelegramChannel) -> None:
    markup = channel.render_payment_prompt(
        payment_url="https://pay.example/1", order_id="order-1", has_qr=True
    )
    flat = [btn for row in markup.inline_keyboard for btn in row]
    assert any(btn.callback_data == "show_qr:order-1" for btn in flat)
    assert any(btn.callback_data == "cancel_payment:order-1" for btn in flat)
    assert len(markup.inline_keyboard) == 4


async def test_send_ticket_codes_sends_short_list_as_single_message(
    channel: TelegramChannel,
) -> None:
    await channel.send_ticket_codes("123", ["ENT-000001", "ENT-000002"])
    channel.bot.send_message.assert_awaited_once()
    _, kwargs = channel.bot.send_message.call_args
    assert kwargs["chat_id"] == "123"
    assert "ENT-000001" in kwargs["text"]
    assert "parse_mode" not in kwargs


async def test_send_ticket_codes_large_list_uses_html_columns(channel: TelegramChannel) -> None:
    codes = [f"ENT-{i:06d}" for i in range(1, 20)]
    await channel.send_ticket_codes("123", codes)
    channel.bot.send_message.assert_awaited()
    _, kwargs = channel.bot.send_message.call_args
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["text"].startswith("<pre>")


async def test_deliver_purchase_with_poster_and_short_codes_sends_single_photo(
    channel: TelegramChannel, tmp_path
) -> None:  # noqa: ANN001
    poster = tmp_path / "poster.png"
    poster.write_bytes(b"\x89PNG\r\n\x1a\n")
    await channel.deliver_purchase(
        "123", poster_path=str(poster), codes=["ENT-000001"], intro="Оплата прошла успешно!"
    )
    channel.bot.send_photo.assert_awaited_once()
    _, kwargs = channel.bot.send_photo.call_args
    assert "ENT-000001" in kwargs["caption"]
    channel.bot.send_message.assert_not_awaited()


async def test_deliver_purchase_without_poster_sends_intro_and_codes(
    channel: TelegramChannel,
) -> None:
    await channel.deliver_purchase(
        "123", poster_path=None, codes=["ENT-000001"], intro="Оплата прошла успешно!"
    )
    channel.bot.send_photo.assert_not_awaited()
    assert channel.bot.send_message.await_count == 2  # интро + коды


def test_telegram_and_vk_active_max_stub_in_production() -> None:
    """Telegram и VK активны (DECISIONS.md #33); MAX остаётся заготовкой (п.21 ТЗ)."""
    assert frozenset({ChannelType.TELEGRAM, ChannelType.VK}) == ACTIVE_CHANNELS
    assert is_channel_active(ChannelType.TELEGRAM) is True
    assert is_channel_active(ChannelType.VK) is True
    assert is_channel_active(ChannelType.MAX) is False


async def test_max_channel_stub_raises_not_implemented() -> None:
    max_cls = get_channel_class(ChannelType.MAX)
    max_channel = max_cls()
    with pytest.raises(NotImplementedError):
        await max_channel.send_media("1", "/tmp/x.png")
