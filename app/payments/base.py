"""BasePaymentProvider — единый интерфейс платёжных провайдеров (п.9.2, 5.4.2 ТЗ).

Реализации: MockProvider (dev/тесты), TBankProvider, VTBProvider. Добавление нового
банка не требует изменений сервисного слоя — только новый класс + регистрация
в фабрике (app/payments/factory.py) + HTTP-роутер для webhook (backend/webhooks/).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import PaymentProviderType, PaymentStatus


@dataclass(frozen=True)
class PaymentOrder:
    """Заказ на создание онлайн-платежа (создаётся сервисным слоем)."""

    order_id: str
    amount: int  # копейки, итого
    unit_price: int  # копейки, цена одного экземпляра — для Receipt.Items[].Price
    quantity: int
    description: str
    participant_phone: str


@dataclass(frozen=True)
class CreatedPayment:
    """Результат создания платежа в банке: куда направить участника для оплаты."""

    payment_url: str
    qr_code_payload: str | None = None  # данные для рендера QR СБП (п.9.1 ТЗ)
    external_payment_id: str | None = None


@dataclass(frozen=True)
class WebhookEvent:
    """Разобранное и провалидированное (подпись проверена) событие webhook банка."""

    order_id: str
    status: PaymentStatus
    raw_payload: dict[str, Any] = field(default_factory=dict)


class WebhookVerificationError(Exception):
    """Некорректная подпись/формат webhook (п.9.5, 17.1 ТЗ — фиксируется в аудите)."""


class BasePaymentProvider(ABC):
    provider_type: PaymentProviderType

    @abstractmethod
    def create_payment(self, order: PaymentOrder) -> CreatedPayment:
        """Создаёт платёж в банке, возвращает ссылку/QR и внешний идентификатор."""

    @abstractmethod
    def verify_and_parse_webhook(self, *, headers: dict[str, str], body: bytes) -> WebhookEvent:
        """Проверяет подпись webhook и парсит его в унифицированную структуру.

        Raises:
            WebhookVerificationError: если подпись неверна или формат не распознан.
        """

    @abstractmethod
    def check_status(
        self, order_id: str, *, external_payment_id: str | None = None
    ) -> PaymentStatus:
        """Резервная проверка статуса платежа напрямую у банка (п.7.5, 9.1 ТЗ).

        `external_payment_id` — `CreatedPayment.external_payment_id`, сохранённый
        при создании платежа (`Payment.external_payment_id`). Некоторые провайдеры
        (Т-Банк `GetState`) требуют именно его, а не `order_id` — см. DECISIONS.md.
        """

    @abstractmethod
    def cancel(self, order_id: str, *, external_payment_id: str | None = None) -> PaymentStatus:
        """Закрывает НЕОПЛАЧЕННУЮ платёжную сессию у банка (не возврат — см. ТЗ §21,
        DECISIONS.md). Реализация обязана сначала убедиться, что платёж
        действительно ещё не оплачен, прежде чем звать банковский метод отмены —
        у некоторых банков (проверено на Т-Банк) отмена уже ПОДТВЕРЖДЁННОГО платежа
        технически исполняется как возврат денег, чего допускать нельзя.

        Возвращает `PaymentStatus.CANCELLED`, если банк подтвердил закрытие
        неоплаченной сессии, либо `PaymentStatus.SUCCEEDED`, если выяснилось, что
        платёж уже был оплачен (гонка) — в этом случае метод НЕ вызывает
        банковскую отмену/возврат, и вызывающая сторона обязана обработать это как
        позднюю успешную оплату (см. `payment_service.finalize_payment`), а не как
        подтверждение отмены.
        """
