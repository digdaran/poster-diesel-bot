"""TelegramChannel — первая реализация `BaseMessengerChannel` (п.10.4 ТЗ).

aiogram 3, long polling. Поддержка HTTP/SOCKS5-прокси (`TELEGRAM_PROXY_URL`).
Вся бизнес-логика остаётся в app/services/* — этот класс только транслирует
UI-примитивы и вызовы Bot API.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import qrcode
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from app.channels.base import BaseMessengerChannel, ChannelCapabilities
from app.models.enums import ChannelType


class TelegramChannel(BaseMessengerChannel):
    channel_type = ChannelType.TELEGRAM
    capabilities = ChannelCapabilities(
        supports_verified_phone=True,
        can_initiate_dialog=True,  # после /start пользователя, см. п.10.4 ТЗ
        supports_inline_buttons=True,
        supports_qr=True,
        media_send_mode="file_id",
    )

    def __init__(self, *, token: str, proxy_url: str | None = None) -> None:
        session = AiohttpSession(proxy=proxy_url) if proxy_url else None
        self.bot = Bot(token=token, session=session)

    async def send_message(self, external_user_id: str, text: str, **kwargs: Any) -> None:
        await self.bot.send_message(chat_id=external_user_id, text=text, **kwargs)

    async def send_media(
        self, external_user_id: str, file_path: str, *, caption: str | None = None
    ) -> None:
        """Отправляет цифровой постер из файла (`Giveaway.digital_poster_path`, п.6.2 ТЗ).

        Кэш `media_id` (`poster_media_cache`) заполняется вызывающей стороной
        (app/services) на основе `message.photo[-1].file_id` из ответа — сам
        `TelegramChannel` не хранит состояние розыгрышей.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Файл постера не найден: {file_path}")
        await self.bot.send_photo(
            chat_id=external_user_id, photo=FSInputFile(path), caption=caption
        )

    async def request_contact(self, external_user_id: str) -> None:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await self.bot.send_message(
            chat_id=external_user_id,
            text="Чтобы подтвердить номер и получить доступ к своим номеркам, поделитесь контактом.",  # noqa: E501
            reply_markup=keyboard,
        )

    def render_keyboard(self, buttons: list[list[str]]) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=label) for label in row] for row in buttons],
            resize_keyboard=True,
        )

    def render_payment_prompt(
        self, *, payment_url: str, order_id: str, has_qr: bool
    ) -> InlineKeyboardMarkup:
        rows = [[InlineKeyboardButton(text="💳 Оплатить", url=payment_url)]]
        if has_qr:
            rows.append(
                [InlineKeyboardButton(text="🔳 Показать QR", callback_data=f"show_qr:{order_id}")]
            )
        rows.append(
            [InlineKeyboardButton(text="🔄 Проверить статус оплаты", callback_data="check_payment")]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def send_qr_code(
        self, external_user_id: str, qr_code_payload: str, *, caption: str | None = None
    ) -> None:
        """QR-код СБП как изображение для оплаты с другого устройства (п.9.1 ТЗ)."""
        img = qrcode.make(qr_code_payload)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")  # type: ignore[call-arg]
        photo = BufferedInputFile(buffer.getvalue(), filename="sbp_qr.png")
        await self.bot.send_photo(chat_id=external_user_id, photo=photo, caption=caption)

    async def handle_update(self, update: Any) -> None:
        """При long polling диспетчеризация обычно идёт напрямую через aiogram
        Dispatcher.start_polling (см. channels/telegram/main.py); этот метод
        используется для программной прогонки одного `Update` (тесты, будущий
        webhook-режим Telegram)."""
        from channels.telegram.dispatcher import get_dispatcher

        dp = get_dispatcher()
        parsed = update if isinstance(update, Update) else Update.model_validate(update)
        await dp.feed_update(self.bot, parsed)
