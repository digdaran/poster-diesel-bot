"""TelegramChannel — первая реализация `BaseMessengerChannel` (п.10.4 ТЗ).

aiogram 3, long polling. Поддержка HTTP/SOCKS5-прокси (`TELEGRAM_PROXY_URL`).
Вся бизнес-логика остаётся в app/services/* — этот класс только транслирует
UI-примитивы и вызовы Bot API.
"""

from __future__ import annotations

import io
from html import escape
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
from aiogram.utils.chat_action import ChatActionSender
from app.channels.base import BaseMessengerChannel, ChannelCapabilities
from app.models.enums import ChannelType

# Запас под HTML-разметку (<pre>...</pre>) и под лимит Telegram в 4096 символов
# на сообщение — превышение лимита иначе приводит к тихому отказу отправки.
_TICKET_CODES_CHUNK_LIMIT = 3500
_TICKET_CODES_COLUMN_THRESHOLD = 10


def _format_ticket_codes(codes: list[str]) -> list[str]:
    """Готовит текст со списком кодов номерков к отправке. До
    `_TICKET_CODES_COLUMN_THRESHOLD` кодов — простой список по одному на
    строку. Больше — моноширинная таблица в несколько колонок (иначе список
    из сотен строк неудобно листать). В обоих случаях результат разбит на
    куски, ни один из которых не превышает лимит Telegram на длину
    сообщения — иначе отправка молча падает."""
    if not codes:
        return []
    if len(codes) <= _TICKET_CODES_COLUMN_THRESHOLD:
        lines = codes
    else:
        width = max(len(c) for c in codes) + 2
        columns = max(1, min(5, 40 // width))
        lines = [
            "".join(c.ljust(width) for c in codes[i : i + columns]).rstrip()
            for i in range(0, len(codes), columns)
        ]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current and current_len + len(line) + 1 > _TICKET_CODES_CHUNK_LIMIT:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


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
            text="Поделитесь контактом, чтобы получить доступ к своим покупкам.",
            reply_markup=keyboard,
        )

    def typing_action(self, chat_id: str) -> ChatActionSender:
        """Показывает "печатает…" на время сетевого запроса к банку/провайдеру
        (aiogram сам повторяет действие каждые ~5с, пока не завершится `async with`)."""
        return ChatActionSender.typing(bot=self.bot, chat_id=chat_id)

    def render_keyboard(self, buttons: list[list[str]]) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=label) for label in row] for row in buttons],
            resize_keyboard=True,
        )

    def render_payment_prompt(self, *, payment_url: str | None) -> InlineKeyboardMarkup | None:
        """QR отправляется отдельным сообщением сразу при создании платежа
        (`send_qr_code`), а статус подтверждается фоновой сверкой и проактивным
        уведомлением (`notification_service`) — своих кнопок для этого больше нет
        (см. DECISIONS_LOG.md). Кнопка-ссылка остаётся, если у провайдера есть
        `payment_url`; сейчас (`RequisitesQrProvider`) его нет, так что клавиатуры
        не будет вовсе."""
        if not payment_url:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить", url=payment_url)]]
        )

    def render_support_prompt(self, *, url: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Написать в поддержку", url=url)]]
        )

    async def send_qr_code(
        self, external_user_id: str, qr_code_payload: str, *, caption: str | None = None
    ) -> None:
        """QR-код для оплаты (СБП-ссылка либо ST00012 по реквизитам, п.9.1 ТЗ,
        ГОСТ Р 56042-2014) как изображение для оплаты с другого устройства.

        Кодируем payload в байты UTF-8 перед рендером — по ГОСТ Р 56042-2014
        префикс "ST00012" сам является декларацией кодировки текста (версия "2"
        = UTF-8, версия "1" = Windows-1251), так что байты обязаны ей
        соответствовать (см. DECISIONS.md; для ASCII-only payload'ов старых
        провайдеров результат не отличается от cp1251/latin1, так что это
        безопасно и для них)."""
        img = qrcode.make(qr_code_payload.encode("utf-8"))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")  # type: ignore[call-arg]
        photo = BufferedInputFile(buffer.getvalue(), filename="sbp_qr.png")
        await self.bot.send_photo(chat_id=external_user_id, photo=photo, caption=caption)

    async def send_ticket_codes(self, external_user_id: str, codes: list[str]) -> None:
        """Отправляет список кодов номерков, разбитый на куски в пределах лимита
        Telegram (см. `_format_ticket_codes`)."""
        multi_column = len(codes) > _TICKET_CODES_COLUMN_THRESHOLD
        for chunk in _format_ticket_codes(codes):
            if multi_column:
                await self.bot.send_message(
                    chat_id=external_user_id, text=f"<pre>{escape(chunk)}</pre>", parse_mode="HTML"
                )
            else:
                await self.bot.send_message(chat_id=external_user_id, text=chunk)

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None:
        """Доставка купленных номерков: постер (если есть) + список кодов, с учётом
        лимита подписи к фото Telegram (1024 симв., отдельно от лимита 4096 на
        обычное сообщение, п.7.5 ТЗ — "отправляется постер... и список кодов")."""
        if poster_path and len(codes) <= _TICKET_CODES_COLUMN_THRESHOLD:
            caption = intro + "\n" + "\n".join(codes) if codes else intro
            await self.send_media(external_user_id, poster_path, caption=caption)
            return
        if poster_path:
            await self.send_media(external_user_id, poster_path, caption=intro)
        else:
            await self.send_message(external_user_id, intro)
        await self.send_ticket_codes(external_user_id, codes)

    async def handle_update(self, update: Any) -> None:
        """При long polling диспетчеризация обычно идёт напрямую через aiogram
        Dispatcher.start_polling (см. channels/telegram/main.py); этот метод
        используется для программной прогонки одного `Update` (тесты, будущий
        webhook-режим Telegram)."""
        from channels.telegram.dispatcher import get_dispatcher

        dp = get_dispatcher()
        parsed = update if isinstance(update, Update) else Update.model_validate(update)
        await dp.feed_update(self.bot, parsed)
