"""Плашка «Telegram недоступен»: переходы состояния проверки канала (DECISIONS_LOG.md №94)."""

from __future__ import annotations

import asyncio

import pytest
from app.services import channel_health_service
from app.services.channel_health_service import ChannelHealth


async def _ok() -> object:
    return object()


async def _fail() -> object:
    raise ConnectionError("Couldn't connect to proxy tg-proxy:1080")


async def test_unknown_until_first_check() -> None:
    health = ChannelHealth()
    assert health.ok is None
    assert health.failing_since is None


async def test_failure_sets_failing_since_once_and_success_resets() -> None:
    health = ChannelHealth()

    await channel_health_service.check(health, _ok)
    assert health.ok is True
    assert health.failing_since is None

    await channel_health_service.check(health, _fail)
    assert health.ok is False
    first_failure = health.failing_since
    assert first_failure is not None
    assert health.last_error is not None and "ConnectionError" in health.last_error

    await channel_health_service.check(health, _fail)
    assert health.failing_since == first_failure  # отсчёт сбоя не сдвигается

    await channel_health_service.check(health, _ok)
    assert health.ok is True
    assert health.failing_since is None
    assert health.last_error is None


async def test_timeout_counts_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channel_health_service, "CHECK_TIMEOUT_SEC", 0.01)

    async def _hang() -> object:
        await asyncio.sleep(10)
        return object()

    health = ChannelHealth()
    await channel_health_service.check(health, _hang)
    assert health.ok is False
    assert health.failing_since is not None
