"""Общий backoff-ретрай для сетевых отправок через мессенджер-каналы.

Появился по факту расследования на проде: VK photo-upload API
(`photos.getMessagesUploadServer` → загрузка → `photos.saveMessagesPhoto`)
под конкурентной нагрузкой на один токен сообщества периодически деградирует —
возвращает пустой `photo` (`VKAPIError_100: photo is undefined`) или
`VKAPIError_10: Internal server error`. Подтверждено экспериментально на
проде: при ≤20 одновременных загрузок отказов практически нет, при 30+ —
единицы процентов, и именно с этой сигнатурой ошибки (см. DECISIONS_LOG.md).

Мгновенный повторный вызов без паузы (как было раньше — `for attempt in
range(2)`) обычно попадает в то же самое окно перегрузки на стороне VK и
проваливается тоже. Экспоненциальный backoff с джиттером даёт нагрузке
на стороне провайдера время спасть, прежде чем следующая попытка."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SEC = 1.5


async def send_with_retry(
    send: Callable[[], Awaitable[None]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SEC,
) -> None:
    """Вызывает `send()` до `attempts` раз подряд с экспоненциальным backoff +
    джиттер между попытками (не после последней). Если все попытки исчерпаны —
    пробрасывает исключение последней попытки, как обычный однократный вызов
    без ретрая; вызывающий код перехватывает его сам (обычно `except Exception:
    logger.exception(...)`, как это уже делалось до ретрая)."""
    for attempt in range(attempts):
        try:
            await send()
            return
        except Exception:
            if attempt == attempts - 1:
                raise
            delay = base_delay * (2**attempt) + random.uniform(0, base_delay / 3)
            await asyncio.sleep(delay)
