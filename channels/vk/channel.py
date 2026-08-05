"""VkChannel — первая реализация `BaseMessengerChannel` для ВКонтакте (бот
сообщества, п.5.4.1, 10.6 ТЗ; план реализации — DECISIONS_LOG.md #32).

vkbottle, VK Bots Long Poll API (без Callback API/вебхука — тот же процесс-
топология, что и у Telegram-канала, см. ARCHITECTURE.md §7.1). Как и
`TelegramChannel`, этот класс только транслирует UI-примитивы и вызовы VK Bot
API — вся бизнес-логика остаётся в app/services/*.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import qrcode
from app.channels.base import BaseMessengerChannel, ChannelCapabilities
from app.models.enums import ChannelType
from vkbottle import (
    API,
    Keyboard,
    OpenLink,
    PhotoMessageUploader,
    Text,
)
from vkbottle.bot import Bot
from vkbottle.polling import BotPolling

# Тот же консервативный запас под лимит длины сообщения, что и у TelegramChannel
# (channels/telegram/channel.py) — единый порог для обоих каналов.
_TICKET_CODES_CHUNK_LIMIT = 3500
_TICKET_CODES_COLUMN_THRESHOLD = 10


def _format_ticket_codes(codes: list[str]) -> list[str]:
    """См. `channels.telegram.channel._format_ticket_codes` — тот же алгоритм
    разбиения списка кодов номерков на куски в пределах лимита сообщения."""
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


class VkChannel(BaseMessengerChannel):
    channel_type = ChannelType.VK
    capabilities = ChannelCapabilities(
        supports_verified_phone=False,
        can_initiate_dialog=True,  # после разрешения "Сообщения сообщества" (DECISIONS_LOG.md #32)
        supports_inline_buttons=True,
        supports_qr=True,
        media_send_mode="upload",
    )

    def __init__(self, *, token: str, group_id: int | None = None) -> None:
        # group_id не обязателен для polling — при отсутствии BotPolling сам
        # резолвит его через groups.getById по токену сообщества (VK_GROUP_ID
        # в .env — опционален для канала с polling). Но `is_messages_allowed`
        # (проактивная сверка разрешений, вне контекста polling — см.
        # backend/background) требует его явно, т.к. без запущенного polling
        # сам не резолвится.
        api = API(token)
        polling = BotPolling(api, group_id=group_id)
        self.bot = Bot(api=api, polling=polling)
        self.group_id = group_id

    async def send_message(self, external_user_id: str, text: str, **kwargs: Any) -> None:
        await self.bot.api.messages.send(
            peer_id=int(external_user_id), message=text, random_id=0, **kwargs
        )

    async def send_media(
        self, external_user_id: str, file_path: str, *, caption: str | None = None
    ) -> None:
        """Отправляет цифровой постер из файла (`Giveaway.digital_poster_path`, п.6.2 ТЗ).

        Upload-флоу VK (`media_send_mode="upload"`): `PhotoMessageUploader`
        сам обходит `photos.getMessagesUploadServer` → загрузку файла →
        `photos.saveMessagesPhoto` и возвращает attachment-строку
        `photo{owner_id}_{id}`, пригодную для кэширования вызывающей стороной
        в `Giveaway.poster_media_cache` наравне с Telegram `file_id`.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Файл постера не найден: {file_path}")
        uploader = PhotoMessageUploader(self.bot.api)
        attachment = await uploader.upload(str(path), peer_id=int(external_user_id))
        await self.bot.api.messages.send(
            peer_id=int(external_user_id),
            message=caption or "",
            attachment=attachment,
            random_id=0,
        )

    async def request_contact(self, external_user_id: str) -> None:
        """У VK нет аналога Telegram `request_contact` с гарантированно
        принадлежащим номером (`supports_verified_phone=False`, см.
        ARCHITECTURE.md §7.1) — явно сообщаем о невозможности вместо запроса,
        как того требует контракт `BaseMessengerChannel.request_contact`."""
        await self.send_message(
            external_user_id,
            "Для продолжения, пожалуйста, напишите свой номер телефона.",
        )

    def render_keyboard(self, buttons: list[list[str]]) -> str:
        keyboard = Keyboard(one_time=False, inline=False)
        for row_index, row in enumerate(buttons):
            if row_index:
                keyboard.row()
            for label in row:
                keyboard.add(Text(label))
        return keyboard.get_json()

    def render_payment_prompt(self, *, payment_url: str | None) -> str | None:
        """См. `channels.telegram.channel.TelegramChannel.render_payment_prompt` —
        та же логика: QR и статус оплаты больше не завязаны на кнопки здесь."""
        if not payment_url:
            return None
        keyboard = Keyboard(inline=True)
        keyboard.add(OpenLink(payment_url, "💳 Оплатить"))
        return keyboard.get_json()

    def render_support_prompt(self, *, url: str) -> str:
        keyboard = Keyboard(inline=True)
        keyboard.add(OpenLink(url, "💬 Написать в поддержку"))
        return keyboard.get_json()

    async def send_qr_code(
        self, external_user_id: str, qr_code_payload: str, *, caption: str | None = None
    ) -> None:
        """QR-код для оплаты (СБП-ссылка либо ST00012 по реквизитам, п.9.1 ТЗ,
        ГОСТ Р 56042-2014) как изображение для оплаты с другого устройства —
        см. `channels.telegram.channel.TelegramChannel.send_qr_code` про
        UTF-8-кодирование payload'а перед рендером (DECISIONS.md)."""
        img = qrcode.make(qr_code_payload.encode("utf-8"))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")  # type: ignore[call-arg]
        buffer.name = "sbp_qr.png"
        uploader = PhotoMessageUploader(self.bot.api)
        attachment = await uploader.upload(buffer, peer_id=int(external_user_id))
        await self.bot.api.messages.send(
            peer_id=int(external_user_id),
            message=caption or "",
            attachment=attachment,
            random_id=0,
        )

    async def send_ticket_codes(self, external_user_id: str, codes: list[str]) -> None:
        """Отправляет список кодов номерков, разбитый на куски в пределах лимита
        сообщения (см. `_format_ticket_codes`)."""
        for chunk in _format_ticket_codes(codes):
            await self.send_message(external_user_id, chunk)

    async def deliver_purchase(
        self, external_user_id: str, *, poster_path: str | None, codes: list[str], intro: str
    ) -> None:
        """Доставка купленных номерков: постер (если есть) + список кодов —
        см. `channels.telegram.channel.TelegramChannel.deliver_purchase`."""
        if poster_path and len(codes) <= _TICKET_CODES_COLUMN_THRESHOLD:
            caption = intro + "\n" + "\n".join(codes) if codes else intro
            await self.send_media(external_user_id, poster_path, caption=caption)
            return
        if poster_path:
            await self.send_media(external_user_id, poster_path, caption=intro)
        else:
            await self.send_message(external_user_id, intro)
        await self.send_ticket_codes(external_user_id, codes)

    async def is_messages_allowed(self, external_user_id: str) -> bool:
        """Живая проверка через `messages.isMessagesFromGroupAllowed` — источник
        истины у самого VK, в отличие от локально закэшенного
        `ChannelBinding.messages_allowed`, который обновляется только пассивно
        по Long Poll `message_allow`/`message_deny` (`on_message_allow`/
        `on_message_deny` в `channels/vk/handlers.py`) и может разойтись с
        реальным состоянием — напр. если пользователь сам начал переписку с
        сообществом первым, VK не всегда шлёт `message_allow` отдельным
        событием, хотя разрешение уже действует (см.
        `backend/background._reconcile_vk_permissions`, DECISIONS_LOG.md №61)."""
        if self.group_id is None:
            raise RuntimeError("VK_GROUP_ID не задан — is_messages_allowed требует явный group_id")
        result = await self.bot.api.messages.is_messages_from_group_allowed(
            group_id=self.group_id, user_id=int(external_user_id)
        )
        return bool(result.is_allowed)

    async def handle_update(self, update: Any) -> None:
        """При Long Poll диспетчеризация обычно идёт напрямую через
        `Bot.run_polling` (см. `channels/vk/main.py`); этот метод — для
        программной прогонки одного сырого события Long Poll (тесты)."""
        await self.bot.process_event(update)
