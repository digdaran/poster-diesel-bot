"""BaseBankStatementProvider — единый интерфейс для получения выписки по расчётному
счёту (входящие операции), используется для подтверждения оплаты по реквизитам
(см. app/services/bank_reconciliation_service.py, RequisitesQrProvider).

Отдельная абстракция от `BasePaymentProvider` (app/payments/base.py): та описывает
провайдера ОДНОГО платежа (создание/статус/отмена конкретной операции у банка-
эквайера), тогда как эта — источник СПИСКА входящих операций по счёту за период,
не привязанный к конкретному платежу нашей системы (сопоставление — отдельный шаг,
см. bank_reconciliation_service.find_matching_entry).
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class BankStatementEntry:
    """Одна входящая операция по расчётному счёту."""

    external_id: str
    amount: int  # копейки
    purpose: str
    operation_date: dt.datetime


class BaseBankStatementProvider(ABC):
    @abstractmethod
    def fetch_operations(self, *, since: dt.datetime) -> list[BankStatementEntry]:
        """Возвращает входящие операции по счёту с `since` по текущий момент."""
