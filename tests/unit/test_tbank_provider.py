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
                unit_price=1000,
                quantity=1,
                description="test",
                participant_phone="79991234567",
            )
        )
    assert created.external_payment_id == "bank-payment-99"


def test_create_payment_includes_receipt(provider: TBankProvider) -> None:
    """Банк требует Receipt в каждом Init для фискализации чека по 54-ФЗ."""
    from app.payments.base import PaymentOrder

    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response(
            {
                "Success": True,
                "PaymentURL": "https://acq.tbank.example/pay/xyz",
                "PaymentId": "bank-payment-99",
            }
        )
        provider.create_payment(
            PaymentOrder(
                order_id="order-1",
                amount=3000,
                unit_price=1000,
                quantity=3,
                description="Постер «X»: 3 шт.",
                participant_phone="79991234567",
            )
        )

    _, kwargs = mock_post.call_args
    receipt = kwargs["json"]["Receipt"]
    assert receipt["Phone"] == "+79991234567"
    assert receipt["Taxation"] == provider.receipt_taxation
    assert receipt["Items"] == [
        {
            "Name": "Постер «X»: 3 шт.",
            "Price": 1000,
            "Quantity": 3,
            "Amount": 3000,
            "Tax": provider.receipt_tax,
            "PaymentMethod": provider.receipt_payment_method,
            "PaymentObject": provider.receipt_payment_object,
        }
    ]


def test_create_payment_receipt_not_included_in_signature(provider: TBankProvider) -> None:
    """Receipt — вложенный объект, банк исключает вложенные поля из подписи Token
    (см. `_sign`) — Token должен совпадать независимо от содержимого Receipt."""
    from app.payments.base import PaymentOrder

    order_a = PaymentOrder(
        order_id="order-1",
        amount=1000,
        unit_price=1000,
        quantity=1,
        description="test",
        participant_phone="79991234567",
    )
    order_b = PaymentOrder(
        order_id="order-1",
        amount=1000,
        unit_price=500,
        quantity=2,
        description="test",
        participant_phone="70000000000",
    )
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response(
            {"Success": True, "PaymentURL": "https://x", "PaymentId": "1"}
        )
        provider.create_payment(order_a)
        token_a = mock_post.call_args.kwargs["json"]["Token"]
        provider.create_payment(order_b)
        token_b = mock_post.call_args.kwargs["json"]["Token"]

    assert token_a == token_b


def test_create_payment_sends_notification_url_when_configured() -> None:
    """Регресс на боевой инцидент: вебхуки ни разу не доходили до backend, а
    личный кабинет банка не гарантированно содержит правильный URL уведомления
    для терминала — теперь передаём его явно в каждом Init-запросе (см.
    DECISIONS.md), не полагаясь на настройки кабинета."""
    from app.payments.base import PaymentOrder

    provider = TBankProvider(
        terminal_key="term-1",
        secret_key="secret-1",
        api_base="https://acq.tbank.example/v2",
        notification_url="https://example.ru/webhooks/tbank",
    )
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response(
            {
                "Success": True,
                "PaymentURL": "https://acq.tbank.example/pay/xyz",
                "PaymentId": "bank-payment-99",
            }
        )
        provider.create_payment(
            PaymentOrder(
                order_id="order-1",
                amount=1000,
                unit_price=1000,
                quantity=1,
                description="test",
                participant_phone="79991234567",
            )
        )

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["NotificationURL"] == "https://example.ru/webhooks/tbank"


def test_create_payment_omits_notification_url_when_not_configured(
    provider: TBankProvider,
) -> None:
    from app.payments.base import PaymentOrder

    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response(
            {
                "Success": True,
                "PaymentURL": "https://acq.tbank.example/pay/xyz",
                "PaymentId": "bank-payment-99",
            }
        )
        provider.create_payment(
            PaymentOrder(
                order_id="order-1",
                amount=1000,
                unit_price=1000,
                quantity=1,
                description="test",
                participant_phone="79991234567",
            )
        )

    _, kwargs = mock_post.call_args
    assert "NotificationURL" not in kwargs["json"]


def test_cancel_checks_status_first_then_cancels_when_still_pending(
    provider: TBankProvider,
) -> None:
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.side_effect = [
            _response({"Success": True, "Status": "NEW"}),  # check_status (GetState)
            _response({"Success": True, "Status": "CANCELED"}),  # Cancel
        ]
        status = provider.cancel("order-1", external_payment_id="bank-payment-42")

    assert status == PaymentStatus.CANCELLED
    assert mock_post.call_count == 2
    getstate_url, cancel_url = (call.args[0] for call in mock_post.call_args_list)
    assert getstate_url.endswith("/GetState")
    assert cancel_url.endswith("/Cancel")
    _, cancel_kwargs = mock_post.call_args_list[1]
    assert cancel_kwargs["json"]["PaymentId"] == "bank-payment-42"


def test_cancel_does_not_call_bank_cancel_when_already_confirmed(
    provider: TBankProvider,
) -> None:
    """Регресс на боевой инцидент: Cancel на уже ПОДТВЕРЖДЁННОМ платеже у
    Т-Банк технически исполняется как возврат денег — must never be called
    blindly. Если check_status уже говорит SUCCEEDED, банковский Cancel не
    вызывается вовсе."""
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.return_value = _response({"Success": True, "Status": "CONFIRMED"})
        status = provider.cancel("order-1", external_payment_id="bank-payment-42")

    assert status == PaymentStatus.SUCCEEDED
    assert mock_post.call_count == 1  # только GetState, Cancel не вызван


def test_cancel_raises_on_bank_error(provider: TBankProvider) -> None:
    with patch("app.payments.tbank.httpx.post") as mock_post:
        mock_post.side_effect = [
            _response({"Success": True, "Status": "NEW"}),
            _response({"Success": False, "ErrorCode": "1", "Message": "Что-то пошло не так"}),
        ]
        with pytest.raises(RuntimeError, match="Cancel отклонён"):
            provider.cancel("order-1", external_payment_id="bank-payment-42")


def test_from_settings_builds_notification_url_from_panel_domain() -> None:
    from app.core.config import Settings

    settings = Settings(
        JWT_SECRET="test-secret-not-for-production-use-only-in-tests",
        SUPERADMIN_PASSWORD="test-password",
        PANEL_DOMAIN="bot.example.ru",
        TBANK_TERMINAL_KEY="term-1",
        TBANK_SECRET_KEY="secret-1",
        TBANK_API_BASE="https://acq.tbank.example/v2",
    )
    provider = TBankProvider.from_settings(settings)
    assert provider.notification_url == "https://bot.example.ru/webhooks/tbank"


def test_from_settings_builds_receipt_config_from_env() -> None:
    from app.core.config import Settings

    settings = Settings(
        JWT_SECRET="test-secret-not-for-production-use-only-in-tests",
        SUPERADMIN_PASSWORD="test-password",
        PANEL_DOMAIN="bot.example.ru",
        TBANK_TERMINAL_KEY="term-1",
        TBANK_SECRET_KEY="secret-1",
        TBANK_API_BASE="https://acq.tbank.example/v2",
        TBANK_RECEIPT_TAXATION="osn",
        TBANK_RECEIPT_TAX="vat20",
        TBANK_RECEIPT_PAYMENT_METHOD="full_prepayment",
        TBANK_RECEIPT_PAYMENT_OBJECT="service",
    )
    provider = TBankProvider.from_settings(settings)
    assert provider.receipt_taxation == "osn"
    assert provider.receipt_tax == "vat20"
    assert provider.receipt_payment_method == "full_prepayment"
    assert provider.receipt_payment_object == "service"
