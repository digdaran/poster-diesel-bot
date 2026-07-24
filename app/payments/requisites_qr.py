"""RequisitesQrProvider — оплата по статическому QR с банковскими реквизитами
(ГОСТ Р 56042-2014, ST00012), см. DECISIONS.md и ARCHITECTURE.md §3/§4.

В отличие от интернет-эквайринга (TBankProvider/VTBProvider): нет сетевого вызова
при создании платежа (`create_payment` — чистая сборка QR-payload), нет банковской
сессии для отмены (`cancel` — no-op), нет вебхука (`verify_and_parse_webhook`
поднимает ошибку — не используется). Подтверждение оплаты идёт асинхронно через
сверку выписки расчётного счёта (см. app/services/bank_reconciliation_service.py),
а `check_status` — разовая версия той же сверки по запросу участника («Проверить
статус оплаты»).
"""

from __future__ import annotations

import datetime as dt

from app.core.config import Settings
from app.core.db import Database
from app.models.enums import PaymentProviderType, PaymentStatus
from app.payments import qr_requisites as qr
from app.payments.base import (
    BasePaymentProvider,
    CreatedPayment,
    PaymentOrder,
    WebhookEvent,
    WebhookVerificationError,
)


class RequisitesQrProvider(BasePaymentProvider):
    provider_type = PaymentProviderType.REQUISITES_QR
    reserves_tickets_on_create = False

    def __init__(
        self,
        *,
        recipient_name: str,
        recipient_inn: str,
        recipient_kpp: str,
        personal_acc: str,
        bank_name: str,
        bic: str,
        corresp_acc: str,
        vat_rate_percent: int,
        db: Database | None = None,
    ) -> None:
        self.recipient_name = recipient_name
        self.recipient_inn = recipient_inn
        self.recipient_kpp = recipient_kpp
        self.personal_acc = personal_acc
        self.bank_name = bank_name
        self.bic = bic
        self.corresp_acc = corresp_acc
        self.vat_rate_percent = vat_rate_percent
        self.db = db

    @classmethod
    def from_settings(
        cls, settings: Settings, *, db: Database | None = None
    ) -> RequisitesQrProvider:
        return cls(
            recipient_name=settings.requisites_recipient_name,
            recipient_inn=settings.requisites_recipient_inn,
            recipient_kpp=settings.requisites_recipient_kpp,
            personal_acc=settings.requisites_personal_acc,
            bank_name=settings.requisites_bank_name,
            bic=settings.requisites_bic,
            corresp_acc=settings.requisites_corr_acc,
            vat_rate_percent=settings.requisites_vat_rate_percent,
            db=db,
        )

    def create_payment(self, order: PaymentOrder) -> CreatedPayment:
        if not order.invoice_no:
            raise ValueError(
                "RequisitesQrProvider требует PaymentOrder.invoice_no "
                "(присваивается payment_service.create_payment до вызова провайдера)"
            )
        purpose = qr.build_purpose(
            invoice_no=order.invoice_no,
            invoice_date=dt.date.today(),
            amount_kopecks=order.amount,
            vat_rate_percent=self.vat_rate_percent,
        )
        payload = qr.build_st00012_payload(
            name=self.recipient_name,
            personal_acc=self.personal_acc,
            bank_name=self.bank_name,
            bic=self.bic,
            corresp_acc=self.corresp_acc,
            payee_inn=self.recipient_inn,
            kpp=self.recipient_kpp or None,
            sum_kopecks=order.amount,
            purpose=purpose,
        )
        return CreatedPayment(
            payment_url=None,
            qr_code_payload=payload,
            # Текстовый номер счёта (не банковский ID — банка тут нет), нужен для
            # разовой сверки check_status(), см. ниже.
            external_payment_id=order.invoice_no,
        )

    def verify_and_parse_webhook(self, *, headers: dict[str, str], body: bytes) -> WebhookEvent:
        raise WebhookVerificationError(
            "RequisitesQrProvider не поддерживает вебхуки — подтверждение идёт "
            "через сверку выписки (см. bank_reconciliation_service)"
        )

    def check_status(
        self, order_id: str, *, external_payment_id: str | None = None
    ) -> PaymentStatus:
        """Разовая сверка по запросу участника (кнопка «Проверить статус оплаты»).

        Фоновая сверка (`bank_reconciliation_service.reconcile`) уже финализирует
        платёж, как только находит совпадение в выписке — эта проверка почти
        всегда просто увидит уже актуальный статус в БД. Отдельный запрос к
        выписке нужен только на случай, если участник нажал кнопку раньше, чем
        сработал следующий тик фоновой сверки.
        """
        if self.db is None or not external_payment_id:
            return PaymentStatus.PENDING

        from sqlalchemy import select

        from app.models.payment import Payment

        with self.db.session() as session:
            payment = session.execute(
                select(Payment).where(Payment.order_id == order_id)
            ).scalar_one_or_none()
            if payment is None:
                return PaymentStatus.PENDING
            if payment.status != PaymentStatus.PENDING:
                return payment.status
            since = payment.created_at
            expected_amount = payment.amount

        from app.payments.tbank_statement import TBankStatementProvider
        from app.services.bank_reconciliation_service import find_matching_entry

        settings = self.db.settings
        try:
            entries = TBankStatementProvider.from_settings(settings).fetch_operations(since=since)
        except Exception:
            return PaymentStatus.PENDING

        result = find_matching_entry(entries, external_payment_id, expected_amount)
        return PaymentStatus.SUCCEEDED if result.matched is not None else PaymentStatus.PENDING

    def cancel(self, order_id: str, *, external_payment_id: str | None = None) -> PaymentStatus:
        """Нет банковской сессии для закрытия — статический QR остаётся валидным
        для оплаты сколько угодно (участник теоретически может оплатить уже
        отменённый счёт). Это не проблема: если деньги всё же придут, фоновая
        сверка найдёт их и обработает существующим механизмом восстановления
        поздней оплаты (`payment_service._recover_late_success`), тем же, что
        уже ловит позднюю оплату после TTL/отмены у остальных провайдеров."""
        return PaymentStatus.CANCELLED
