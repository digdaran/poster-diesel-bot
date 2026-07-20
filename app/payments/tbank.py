"""TBankProvider — интеграция с Т-Банк Эквайринг API v2 (п.9.2 ТЗ).

Реализовано по публичной документации Т-Банк (Тинькофф) Эквайринг API v2:
метод подписи запроса (Token = SHA-256 от конкатенации отсортированных по ключу
значений плоских параметров + Password), методы `Init` (создание платежа),
`GetState` (резервная проверка статуса), нотификация (`webhook`) с той же схемой
подписи. Не прогонялось против боевого/sandbox-контура банка — см. DECISIONS.md
(нет тестовых ключей); тесты используют MockProvider.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

from app.core.config import Settings
from app.models.enums import PaymentProviderType, PaymentStatus
from app.payments.base import (
    BasePaymentProvider,
    CreatedPayment,
    PaymentOrder,
    WebhookEvent,
    WebhookVerificationError,
)

# Соответствие статусов Т-Банк -> внутренние статусы платежа (документация Т-Банк Эквайринг API v2).
_SUCCESS_STATUSES = {"CONFIRMED"}
_FAILED_STATUSES = {"REJECTED", "CANCELED", "DEADLINE_EXPIRED", "REVERSED", "REFUNDED"}


class TBankProvider(BasePaymentProvider):
    provider_type = PaymentProviderType.TBANK

    def __init__(
        self,
        *,
        terminal_key: str,
        secret_key: str,
        api_base: str,
        notification_url: str | None = None,
    ) -> None:
        self.terminal_key = terminal_key
        self.secret_key = secret_key
        self.api_base = api_base.rstrip("/")
        self.notification_url = notification_url

    @classmethod
    def from_settings(cls, settings: Settings) -> TBankProvider:
        return cls(
            terminal_key=settings.tbank_terminal_key,
            secret_key=settings.tbank_secret_key,
            api_base=settings.tbank_api_base,
            notification_url=f"https://{settings.panel_domain}/webhooks/tbank",
        )

    def _sign(self, params: dict[str, Any]) -> str:
        """Token = SHA-256(конкатенация значений плоских полей, отсортированных по ключу,
        с добавлением Password=secret_key). Вложенные объекты (Receipt, DATA) в подпись
        не включаются — согласно документации Т-Банк."""
        flat = {k: v for k, v in params.items() if not isinstance(v, dict | list)}
        flat["Password"] = self.secret_key
        concatenated = "".join(str(flat[k]) for k in sorted(flat.keys()))
        return hashlib.sha256(concatenated.encode("utf-8")).hexdigest()

    def create_payment(self, order: PaymentOrder) -> CreatedPayment:
        request_body: dict[str, Any] = {
            "TerminalKey": self.terminal_key,
            "Amount": order.amount,
            "OrderId": order.order_id,
            "Description": order.description,
        }
        if self.notification_url:
            # Явно передаём URL уведомления в каждом запросе — не полагаемся на
            # то, что оно настроено (и настроено верно) в личном кабинете банка
            # по умолчанию для терминала (см. DECISIONS.md — вебхуки никогда не
            # доходили, а личный кабинет не гарантированно содержит этот URL).
            request_body["NotificationURL"] = self.notification_url
        request_body["Token"] = self._sign(request_body)

        response = httpx.post(f"{self.api_base}/Init", json=request_body, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        if not data.get("Success"):
            raise RuntimeError(
                f"Т-Банк Init отклонён: {data.get('Message')} / {data.get('Details')}"
            )

        return CreatedPayment(
            payment_url=data["PaymentURL"],
            qr_code_payload=None,  # QR СБП — отдельный метод GetQr, добавляется по необходимости
            external_payment_id=str(data.get("PaymentId", "")),
        )

    def check_status(
        self, order_id: str, *, external_payment_id: str | None = None
    ) -> PaymentStatus:
        """`GetState` требует `PaymentId` (внутренний ID платежа у Т-Банк) —
        `OrderId` этот метод не принимает (проверено напрямую на боевом контуре:
        без PaymentId банк отвечает `Success:false, ErrorCode:201`, что раньше
        молча трактовалось как PENDING — см. DECISIONS.md). `PaymentId`
        сохраняется при создании платежа как `CreatedPayment.external_payment_id`."""
        if not external_payment_id:
            raise ValueError(
                f"Т-Банк GetState требует PaymentId, но платёж {order_id} создан без него "
                "(см. DECISIONS.md — миграция external_payment_id)"
            )
        request_body = {"TerminalKey": self.terminal_key, "PaymentId": external_payment_id}
        request_body["Token"] = self._sign(request_body)
        response = httpx.post(f"{self.api_base}/GetState", json=request_body, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        if not data.get("Success"):
            raise RuntimeError(
                f"Т-Банк GetState отклонён: {data.get('Message')} / {data.get('Details')}"
            )
        return self._map_status(data.get("Status", ""))

    @staticmethod
    def _map_status(bank_status: str) -> PaymentStatus:
        if bank_status in _SUCCESS_STATUSES:
            return PaymentStatus.SUCCEEDED
        if bank_status in _FAILED_STATUSES:
            return PaymentStatus.FAILED
        return PaymentStatus.PENDING

    def verify_and_parse_webhook(self, *, headers: dict[str, str], body: bytes) -> WebhookEvent:
        try:
            data: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError as exc:
            raise WebhookVerificationError(f"Webhook Т-Банк: некорректный JSON: {exc}") from exc

        received_token = data.get("Token", "")
        expected_token = self._sign({k: v for k, v in data.items() if k != "Token"})
        if received_token != expected_token:
            raise WebhookVerificationError("Webhook Т-Банк: неверная подпись Token")

        order_id = data.get("OrderId")
        if not order_id:
            raise WebhookVerificationError("Webhook Т-Банк: отсутствует OrderId")

        status = self._map_status(str(data.get("Status", "")))
        return WebhookEvent(order_id=str(order_id), status=status, raw_payload=data)
