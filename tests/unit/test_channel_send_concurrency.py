"""Тесты предела одновременных исходящих отправок (`send_concurrency_limit`,
`asyncio.Semaphore` в `TelegramChannel`/`VkChannel`, DECISIONS_LOG.md №72) —
введён после того, как эксперимент на проде показал, что VK photo-upload API
деградирует под конкурентной нагрузкой на один токен сообщества (≤20
одновременных загрузок — практически без отказов, 30+ — единицы процентов
"photo is undefined"). Проверяем, что семафор реально ограничивает пиковую
конкурентность в пределах процесса, а не просто существует."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from channels.telegram.channel import TelegramChannel
from channels.vk.channel import VkChannel


class _ConcurrencyTracker:
    """Считает, сколько вызовов одновременно "внутри" отслеживаемого
    ресурса — используется как side_effect мока сетевого вызова, чтобы
    поймать реальный пик конкурентности, а не просто число вызовов."""

    def __init__(self, *, hold_sec: float = 0.05) -> None:
        self.current = 0
        self.peak = 0
        self.hold_sec = hold_sec

    async def __call__(self, *args: object, **kwargs: object) -> dict:
        self.current += 1
        self.peak = max(self.peak, self.current)
        await asyncio.sleep(self.hold_sec)
        self.current -= 1
        return {"response": 1}


async def test_telegram_semaphore_caps_peak_concurrent_sends() -> None:
    channel = TelegramChannel(token="123456:test-token-not-real", send_concurrency_limit=2)
    tracker = _ConcurrencyTracker()
    channel.bot.send_message = AsyncMock(side_effect=tracker.__call__)  # type: ignore[method-assign]

    await asyncio.gather(*(channel.send_message(str(i), "текст") for i in range(8)))

    assert tracker.peak <= 2
    assert channel.bot.send_message.await_count == 8


async def test_telegram_semaphore_of_one_fully_serializes_sends() -> None:
    """Крайний случай (лимит 1) — все отправки строго последовательны, пик
    конкурентности не может подняться выше 1 ни при каких обстоятельствах."""
    channel = TelegramChannel(token="123456:test-token-not-real", send_concurrency_limit=1)
    tracker = _ConcurrencyTracker()
    channel.bot.send_message = AsyncMock(side_effect=tracker.__call__)  # type: ignore[method-assign]

    await asyncio.gather(*(channel.send_message(str(i), "текст") for i in range(5)))

    assert tracker.peak == 1


async def test_vk_semaphore_caps_peak_concurrent_sends() -> None:
    channel = VkChannel(token="test-group-token", group_id=1, send_concurrency_limit=2)
    tracker = _ConcurrencyTracker()
    channel.bot.api.request = AsyncMock(side_effect=tracker.__call__)  # type: ignore[method-assign]

    await asyncio.gather(*(channel.send_message(str(i), "текст") for i in range(8)))

    assert tracker.peak <= 2
    assert channel.bot.api.request.await_count == 8


async def test_default_concurrency_limit_does_not_serialize_small_batches() -> None:
    """Дефолт (8) не должен мешать обычной небольшой параллельной нагрузке —
    иначе лимит сам по себе стал бы новым источником задержек."""
    channel = TelegramChannel(token="123456:test-token-not-real")
    tracker = _ConcurrencyTracker(hold_sec=0.05)
    channel.bot.send_message = AsyncMock(side_effect=tracker.__call__)  # type: ignore[method-assign]

    await asyncio.gather(*(channel.send_message(str(i), "текст") for i in range(5)))

    assert tracker.peak == 5  # все 5 уложились параллельно в дефолтный лимит 8
