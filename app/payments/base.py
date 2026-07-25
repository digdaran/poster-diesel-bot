"""BasePaymentProvider — единый интерфейс платёжных провайдеров (п.9.2, 5.4.2 ТЗ).

Единственная реализация — `RequisitesQrProvider` (оплата по банковским реквизитам,
см. DECISIONS.md). Интернет-эквайринг (TBankProvider/VTBProvider/MockProvider)
удалён по прямому запросу заказчика (DECISIONS.md №44) — этот интерфейс
исторически абстрагировал несколько банков, но добавление нового провайдера
по-прежнему не требует изменений сервисного слоя, только новый класс +
регистрация в фабрике (app/payments/factory.py).
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
    # Номер счёта на оплату (PREFIX-NNNNN, см. Giveaway.format_invoice_number) —
    # заполняется только для провайдеров без резервирования "на лету"
    # (см. BasePaymentProvider.reserves_tickets_on_create); нужен RequisitesQrProvider
    # для назначения платежа. None для интернет-эквайринга (не используется).
    invoice_no: str | None = None


@dataclass(frozen=True)
class CreatedPayment:
    """Результат создания платежа: куда направить участника для оплаты.

    `payment_url` — `None` у провайдеров без ссылки на оплату (напр.
    `RequisitesQrProvider` — только статический QR по реквизитам)."""

    payment_url: str | None
    qr_code_payload: str | None = None  # данные для рендера QR (СБП/ST00012)
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

    # Резервирует ли провайдер номерки АТОМАРНО в момент создания платежа
    # (см. app/services/payment_service.py::create_payment). Исторически True
    # было для интернет-эквайринга (деньги подтверждаются почти сразу, резерв на
    # короткий TTL оправдан) — с его удалением (DECISIONS.md №44) эта ветка
    # больше не срабатывает ни для одного реального провайдера, но сознательно
    # оставлена как мёртвая, безвредная инфраструктура. `RequisitesQrProvider`
    # переопределяет в `False` — деньги по банковскому переводу могут идти
    # несколько дней, номерки выдаются только по факту зачисления (DECISIONS.md).
    reserves_tickets_on_create: bool = True

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
