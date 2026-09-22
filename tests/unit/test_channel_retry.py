"""Тесты общего backoff-ретрая `app/channels/retry.py::send_with_retry`
(DECISIONS_LOG.md №72) — введён после того, как логи прода показали, что
VK photo-upload API под конкурентной нагрузкой на токен сообщества
периодически деградирует, а мгновенный повтор без паузы обычно попадает
в то же самое окно перегрузки и проваливается тоже."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from app.channels.retry import send_with_retry


async def test_succeeds_on_first_try_without_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("app.channels.retry.asyncio.sleep", sleep)
    send = AsyncMock()

    await send_with_retry(send)

    send.assert_awaited_once()
    sleep.assert_not_awaited()


async def test_succeeds_after_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("app.channels.retry.asyncio.sleep", sleep)
    send = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom"), None])

    await send_with_retry(send)

    assert send.await_count == 3
    assert sleep.await_count == 2  # backoff между 1→2 и 2→3, не после последней


async def test_raises_last_exception_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("app.channels.retry.asyncio.sleep", sleep)
    errors = [RuntimeError("first"), RuntimeError("second"), RuntimeError("last")]
    send = AsyncMock(side_effect=errors)

    with pytest.raises(RuntimeError, match="last"):
        await send_with_retry(send)

    assert send.await_count == 3
    assert sleep.await_count == 2


async def test_respects_custom_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("app.channels.retry.asyncio.sleep", sleep)
    send = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await send_with_retry(send, attempts=5)

    assert send.await_count == 5
    assert sleep.await_count == 4


async def test_backoff_delay_grows_between_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Экспоненциальный рост базовой задержки (джиттер поверх неё — отдельная
    случайная добавка, но нижняя граница каждой попытки должна расти)."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.channels.retry.asyncio.sleep", fake_sleep)
    send = AsyncMock(side_effect=[RuntimeError("a"), RuntimeError("b"), None])

    await send_with_retry(send, base_delay=1.0)

    assert len(delays) == 2
    assert delays[0] >= 1.0  # 1.0 * 2**0 + jitter[0, 1/3)
    assert delays[1] >= 2.0  # 1.0 * 2**1 + jitter[0, 1/3)
    assert delays[1] > delays[0]
