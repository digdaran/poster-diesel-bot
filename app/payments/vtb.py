"""VTBProvider — заготовка интеграции с ВТБ Эквайринг (п.9.2 ТЗ).

В отличие от TBankProvider, публичная унифицированная спецификация API ВТБ
Эквайринга не бралась за основу 1:1 (нет тестовых ключей/sandbox — см.
DECISIONS.md, п.1, согласовано с заказчиком). Класс полностью реализует интерфейс
`BasePaymentProvider` и корректно регистрируется в фабрике, но сетевые вызовы
оставлены как явный `NotImplementedError` с точным описанием, что нужно доделать
при получении реальных доступов банка: URL хоста, формат запроса/подписи,
маппинг статусов. Структура (аргументы, форма ответа) — по аналогии с
TBankProvider, чтобы подключение свелось к реализации трёх методов ниже.
"""

from __future__ import annotations

from app.core.config import Settings
from app.models.enums import PaymentProviderType, PaymentStatus
from app.payments.base import (
    BasePaymentProvider,
    CreatedPayment,
    PaymentOrder,
    WebhookEvent,
)

_NOT_IMPLEMENTED_MSG = (
    "VTBProvider: реализация не завершена — нет тестовых ключей ВТБ Эквайринга "
    "(см. DECISIONS.md, п.1). Для подключения реального банка реализуйте метод "
    "по документации ВТБ API, аналогично TBankProvider."
)


class VTBProvider(BasePaymentProvider):
    provider_type = PaymentProviderType.VTB

    def __init__(self, *, merchant_id: str, secret_key: str, api_base: str) -> None:
        self.merchant_id = merchant_id
        self.secret_key = secret_key
        self.api_base = api_base.rstrip("/")

    @classmethod
    def from_settings(cls, settings: Settings) -> VTBProvider:
        return cls(
            merchant_id=settings.vtb_merchant_id,
            secret_key=settings.vtb_secret_key,
            api_base=settings.vtb_api_base,
        )

    def create_payment(self, order: PaymentOrder) -> CreatedPayment:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def check_status(self, order_id: str) -> PaymentStatus:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def verify_and_parse_webhook(self, *, headers: dict[str, str], body: bytes) -> WebhookEvent:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
