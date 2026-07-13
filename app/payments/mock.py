"""MockProvider — платёжный провайдер для локальной разработки и тестов (без сети).

Позволяет полностью прогонять онлайн-цикл «оплата -> финализация -> выдача» без
реальных банковских ключей (см. промпт реализации: обязателен для dev/тестов).
Состояние храним в памяти процесса — этого достаточно для тестов и локального dev,
т.к. mock не переживает рестарт процесса (в отличие от реальных банков через webhook).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from app.core.config import Settings
from app.models.enums import PaymentProviderType, PaymentStatus
from app.payments.base import (
    BasePaymentProvider,
    CreatedPayment,
    PaymentOrder,
    WebhookEvent,
    WebhookVerificationError,
)

_MOCK_SECRET = "mock-provider-shared-secret"


class MockProvider(BasePaymentProvider):
    provider_type = PaymentProviderType.MOCK

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        # order_id -> статус, который вернёт check_status / примет webhook.
        # По умолчанию новый заказ "зависает" в PENDING, пока тест/дев явно
        # не переведёт его через set_status() либо сгенерированный webhook.
        self._statuses: dict[str, PaymentStatus] = {}

    def create_payment(self, order: PaymentOrder) -> CreatedPayment:
        self._statuses[order.order_id] = PaymentStatus.PENDING
        return CreatedPayment(
            payment_url=f"https://mock-provider.local/pay/{order.order_id}",
            qr_code_payload=f"mock-sbp-qr:{order.order_id}:{order.amount}",
            external_payment_id=f"mock-{order.order_id}",
        )

    def set_status(self, order_id: str, status: PaymentStatus) -> None:
        """Тестовый хук: имитирует решение банка (используется в тестах вместо
        реального ожидания оплаты)."""
        self._statuses[order_id] = status

    def check_status(self, order_id: str) -> PaymentStatus:
        return self._statuses.get(order_id, PaymentStatus.PENDING)

    def build_webhook_payload(self, order_id: str, status: PaymentStatus) -> bytes:
        """Формирует подписанное тело webhook — для тестов роутера webhook."""
        payload = {"order_id": order_id, "status": status.value}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return body

    def _sign(self, body: bytes) -> str:
        return hmac.new(_MOCK_SECRET.encode(), body, hashlib.sha256).hexdigest()

    def sign_headers(self, body: bytes) -> dict[str, str]:
        return {"X-Mock-Signature": self._sign(body)}

    def verify_and_parse_webhook(self, *, headers: dict[str, str], body: bytes) -> WebhookEvent:
        signature = headers.get("X-Mock-Signature", "")
        expected = self._sign(body)
        if not hmac.compare_digest(signature, expected):
            raise WebhookVerificationError("Неверная подпись mock-webhook")
        try:
            data: dict[str, Any] = json.loads(body)
            order_id = str(data["order_id"])
            status = PaymentStatus(data["status"])
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise WebhookVerificationError(f"Некорректный формат webhook: {exc}") from exc
        self._statuses[order_id] = status
        return WebhookEvent(order_id=order_id, status=status, raw_payload=data)
