"""Webhook банка через HTTP (п.9.4, 9.5, 17.1, 20.1 ТЗ): т.к. активный провайдер
в тестах — mock, а вебхук-роутеры принимают только tbank/vtb, здесь проверяем
контракт отклонения некорректной подписи/данных на реальном HTTP-эндпоинте
(TBankProvider с пустыми тестовыми ключами -> подпись не совпадёт)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_tbank_webhook_rejects_invalid_signature(api_client: TestClient) -> None:
    resp = api_client.post(
        "/webhooks/tbank",
        json={
            "TerminalKey": "x",
            "OrderId": "unknown-order",
            "Status": "CONFIRMED",
            "Token": "bad-token",
        },
    )
    assert resp.status_code == 400


def test_vtb_webhook_not_implemented_raises_clear_error(api_client: TestClient) -> None:
    """VTBProvider — заготовка (см. DECISIONS.md): нет тестовых ключей ВТБ, сетевые
    методы поднимают NotImplementedError, а не тихо обрабатывают произвольные данные."""
    with pytest.raises(NotImplementedError):
        api_client.post("/webhooks/vtb", json={"foo": "bar"})
