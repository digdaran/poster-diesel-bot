"""Точка входа процесса `channel-telegram` (п.5.1, 10.4, 19 ТЗ): aiogram 3,
long polling, отдельный процесс/образ со своими зависимостями."""

from __future__ import annotations

import asyncio

import structlog
from app.core.config import get_settings

from channels.telegram.channel import TelegramChannel
from channels.telegram.dispatcher import get_dispatcher
from channels.telegram.handlers import set_channel

logger = structlog.get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан — канал Telegram не может быть запущен")

    channel = TelegramChannel(
        token=settings.telegram_bot_token, proxy_url=settings.telegram_proxy_url or None
    )
    set_channel(channel)

    dp = get_dispatcher()
    logger.info("telegram_channel_starting")
    await dp.start_polling(channel.bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
