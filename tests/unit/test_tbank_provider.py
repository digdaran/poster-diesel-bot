"""Тесты `TBankProvider.check_status` (см. DECISIONS.md): регресс на реальный
боевой инцидент — `GetState` у Т-Банк требует `PaymentId` (внутренний ID платежа
банка), а не наш `order_id`; без него банк отвечает `Success:false`, что раньше
молча трактовалось как PENDING и делало резервную сверку/ручную проверку
неработоспособной для ВСЕХ Т-Банк-платежей."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
from app.models.enums import PaymentStatus
from app.payments.tbank import TBankProvider


def _response(json_body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200, json=json_body, request=httpx.Request("POST", "https://acq.tbank.example/v2/GetState")
    )


@pytest.fixture
def provider() -> TBankProvider:
    return TBankProvider(
        terminal_key="term-1", secret_key="secret-1", api_base="https://acq.tbank.example/v2"
    )


def test_check_status_raises_without_external_payment_id(provider: TBankProvider) -> None:
    with pytest.raises(ValueError, match="PaymentId"):
        provider.check_status("order-1")


def test_check_status_sends_payment_id_not_order_id(provider: TBankProvider) -> None:
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response({"Success": True, "Status": "CONFIRMED"})
        provider.check_status("order-1", external_payment_id="bank-payment-42")

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["PaymentId"] == "bank-payment-42"
    assert "OrderId" not in body


def test_check_status_maps_confirmed_to_succeeded(provider: TBankProvider) -> None:
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response({"Success": True, "Status": "CONFIRMED"})
        status = provider.check_status("order-1", external_payment_id="bank-payment-42")
    assert status == PaymentStatus.SUCCEEDED


def test_check_status_raises_on_bank_error_instead_of_masking_as_pending(
    provider: TBankProvider,
) -> None:
    """Регресс на боевой инцидент: без PaymentId банк отвечает Success:false —
    раньше это тихо превращалось в PENDING навсегда, теперь — явная ошибка,
    видимая в логах фоновой сверки."""
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response(
            {
                "Success": False,
                "ErrorCode": "201",
                "Message": "Неверные параметры.",
                "Details": "Поле PaymentId не должно быть пустым.",
            }
        )
        with pytest.raises(RuntimeError, match="GetState отклонён"):
            provider.check_status("order-1", external_payment_id="bank-payment-42")


def test_check_status_maps_rejected_to_failed(provider: TBankProvider) -> None:
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response({"Success": True, "Status": "REJECTED"})
        status = provider.check_status("order-1", external_payment_id="bank-payment-42")
    assert status == PaymentStatus.FAILED


def test_check_status_maps_intermediate_status_to_pending(provider: TBankProvider) -> None:
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response({"Success": True, "Status": "AUTHORIZING"})
        status = provider.check_status("order-1", external_payment_id="bank-payment-42")
    assert status == PaymentStatus.PENDING


def test_create_payment_extracts_external_payment_id(provider: TBankProvider) -> None:
    from app.payments.base import PaymentOrder

    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response(
            {
                "Success": True,
                "PaymentURL": "https://acq.tbank.example/pay/xyz",
                "PaymentId": "bank-payment-99",
            }
        )
        created = provider.create_payment(
            PaymentOrder(
                order_id="order-1",
                amount=1000,
                quantity=1,
                description="test",
                participant_phone="79991234567",
            )
        )
    assert created.external_payment_id == "bank-payment-99"
