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
на стороне провайдера время спасть, прежде чем следующая попытка.

Не всякий сбой стоит повторять: если получатель заблокировал бота или его
аккаунт удалён, ответ провайдера будет ИДЕНТИЧНЫМ на любой попытке — ни
задержка, ни число попыток этого не изменят (найдено на проде при первой
боевой рассылке — см. `app/services/channel_delivery_queue.py::_is_permanent_failure`,
DECISIONS_LOG.md). `is_permanent` даёт вызывающему коду способ прервать
ретраи досрочно для таких случаев, не тратя оставшиеся попытки и backoff
впустую."""

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
    is_permanent: Callable[[Exception], bool] | None = None,
) -> None:
    """Вызывает `send()` до `attempts` раз подряд с экспоненциальным backoff +
    джиттер между попытками (не после последней). Если все попытки исчерпаны —
    пробрасывает исключение последней попытки, как обычный однократный вызов
    без ретрая; вызывающий код перехватывает его сам (обычно `except Exception:
    logger.exception(...)`, как это уже делалось до ретрая). `is_permanent`
    (опционально) — прерывает ретраи сразу на первой же попытке, если сбой
    заведомо не исправится повтором (пробрасывает то же исключение, не дожидаясь
    остальных попыток)."""
    for attempt in range(attempts):
        try:
            await send()
            return
        except Exception as exc:
            if attempt == attempts - 1 or (is_permanent is not None and is_permanent(exc)):
                raise
            delay = base_delay * (2**attempt) + random.uniform(0, base_delay / 3)
            await asyncio.sleep(delay)
