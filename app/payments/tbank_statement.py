"""TBankStatementProvider — выписка по расчётному счёту через Т-Банк T-API
(`GET /api/v1/statement`, developer.tbank.ru/docs/api/get-api-v-1-statement).

Реализовано по публичной документации, БЕЗ реального прогона против боевого/sandbox
контура Т-Бизнес (нет тестовых доступов) — тот же принятый в проекте риск, что и
для `TBankProvider`/`VTBProvider` интернет-эквайринга (см. DECISIONS.md №1).

Точные имена полей ОДНОЙ операции в ответе (сумма/назначение платежа/дата/id) не
подтверждены документацией без реального доступа к API — `_parse_entry` защищённо
перебирает несколько правдоподобных вариантов ключей и пропускает (с предупреждением
в лог) операции, которые не удалось разобрать, вместо падения. **Заказчик обязан
сверить реальный JSON-ответ API и поправить маппинг перед продакшеном.**
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import structlog

from app.core.config import Settings
from app.payments.bank_statement import BankStatementEntry, BaseBankStatementProvider

logger = structlog.get_logger(__name__)

# Правдоподобные варианты ключей в ответе T-API (не подтверждены документацией без
# реального доступа — см. модуль docstring).
_OPERATIONS_LIST_KEYS = ("operations", "items", "data", "payload")
_ID_KEYS = ("operationId", "id", "documentNumber")
_PURPOSE_KEYS = ("paymentPurpose", "purpose", "description", "narrative")
_DATE_KEYS = ("operationDate", "valueDate", "date", "createdAt")
_DIRECTION_KEYS = ("operationType", "direction", "type")
_CREDIT_MARKERS = {"credit", "incoming", "in", "receipt"}
_NEXT_CURSOR_KEYS = ("nextCursor", "cursor", "next_cursor")


class TBankStatementProvider(BaseBankStatementProvider):
    def __init__(self, *, api_base: str, api_token: str, account_number: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_token = api_token
        self.account_number = account_number

    @classmethod
    def from_settings(cls, settings: Settings) -> TBankStatementProvider:
        return cls(
            api_base=settings.tbank_statement_api_base,
            api_token=settings.tbank_statement_api_token,
            account_number=settings.tbank_statement_account_number,
        )

    def fetch_operations(self, *, since: dt.datetime) -> list[BankStatementEntry]:
        entries: list[BankStatementEntry] = []
        cursor: str | None = None
        headers = {"Authorization": f"Bearer {self.api_token}"}
        now = dt.datetime.now(dt.timezone.utc)

        while True:
            params: dict[str, Any] = {
                "accountNumber": self.account_number,
                "from": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 1000,
            }
            if cursor:
                params["cursor"] = cursor

            response = httpx.get(
                f"{self.api_base}/statement", params=params, headers=headers, timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

            for raw_operation in _extract_operations(data):
                entry = _parse_entry(raw_operation)
                if entry is not None:
                    entries.append(entry)
                else:
                    logger.warning("tbank_statement_entry_unparseable", raw=raw_operation)

            cursor = _extract_next_cursor(data)
            if not cursor:
                break

        return entries


def _extract_operations(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in _OPERATIONS_LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_operations(value)
            if nested:
                return nested
    return []


def _extract_next_cursor(data: dict[str, Any]) -> str | None:
    for key in _NEXT_CURSOR_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_present(operation: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in operation and operation[key] is not None:
            return operation[key]
    return None


def _parse_amount(raw_amount: Any) -> int | None:
    """Возвращает сумму в копейках. Т-API может отдавать сумму как число (рубли
    или копейки — не подтверждено) либо как объект `{"value": ..., "currency": ...}`."""
    if isinstance(raw_amount, dict):
        raw_amount = raw_amount.get("value")
    if raw_amount is None:
        return None
    try:
        value = float(raw_amount)
    except (TypeError, ValueError):
        return None
    # Эвристика: дробное значение — рубли (нужно перевести в копейки), целое без
    # дробной части лечится как уже копейки. Требует проверки на реальном ответе API.
    if value != int(value):
        return round(value * 100)
    return int(value)


def _parse_entry(operation: dict[str, Any]) -> BankStatementEntry | None:
    direction = str(_first_present(operation, _DIRECTION_KEYS) or "").lower()
    if direction and not any(marker in direction for marker in _CREDIT_MARKERS):
        return None  # похоже на исходящую операцию — не входящий платёж участника

    external_id = _first_present(operation, _ID_KEYS)
    purpose = _first_present(operation, _PURPOSE_KEYS)
    raw_date = _first_present(operation, _DATE_KEYS)
    amount = _parse_amount(operation.get("amount"))

    if external_id is None or purpose is None or raw_date is None or amount is None:
        return None

    operation_date = _parse_date(str(raw_date))
    if operation_date is None:
        return None

    return BankStatementEntry(
        external_id=str(external_id),
        amount=amount,
        purpose=str(purpose),
        operation_date=operation_date,
    )


def _parse_date(raw: str) -> dt.datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=None)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
