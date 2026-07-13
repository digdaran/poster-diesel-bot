"""Единый Dispatcher aiogram для Telegram-канала (long polling и программная
прогонка update через `TelegramChannel.handle_update`)."""

from __future__ import annotations

from functools import lru_cache

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from channels.telegram.handlers import router


@lru_cache
def get_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp
