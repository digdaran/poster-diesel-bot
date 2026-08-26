"""Pydantic-схемы запросов/ответов API панели."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    login: str
    password: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: int
    login: str
    role: str
    permissions: list[str]


class ParticipantOut(BaseModel):
    id: int
    phone: str
    phone_verified: bool
    full_name: str | None
    is_blocked: bool
    channels: list[str]
    created_at: dt.datetime
    # Агрегаты считаются в list_participants (не хранятся на модели) и
    # проставляются поверх model_validate — дефолты нужны, чтобы
    # model_validate(participant) не падал на отсутствующем атрибуте.
    total_tickets: int = 0
    active_tickets: int = 0

    model_config = {"from_attributes": True}


class ParticipantUpdateRequest(BaseModel):
    full_name: str | None = None
    phone: str | None = None


class GiveawayOut(BaseModel):
    id: int
    name: str
    prefix: str
    ticket_price: int
    max_tickets: int
    tickets_issued: int
    tickets_reserved: int
    is_registration_open: bool
    is_locked: bool
    is_archived: bool
    archived_at: dt.datetime | None
    opened_at: dt.datetime | None
    digital_poster_caption: str | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class GiveawayCreateRequest(BaseModel):
    name: str
    prefix: str = Field(min_length=1, max_length=20)
    ticket_price: int = Field(gt=0)
    max_tickets: int = Field(gt=0, le=100_000)
    digital_poster_caption: str | None = None


class GiveawayUpdateRequest(BaseModel):
    """Разрешено менять ТОЛЬКО название/постер/подпись после открытия регистрации
    (п.7.2 ТЗ) — prefix/ticket_price/max_tickets неизменяемы."""

    name: str | None = None
    digital_poster_caption: str | None = None


class GiveawayPosterOut(BaseModel):
    id: int
    giveaway_id: int
    original_filename: str | None
    content_type: str | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class ManualRegistrationOut(BaseModel):
    id: int
    participant_id: int
    participant_phone: str
    participant_full_name: str | None
    participant_channels: list[str]
    giveaway_id: int
    giveaway_name: str
    quantity: int
    revenue: int
    status: str
    operator_id: int
    operator_login: str
    comment: str | None
    payment_method: str
    invoice_no: str | None
    """Номер счёта (PREFIX-NNNNN) — заполнен только после генерации QR (payment_method=CASHLESS)."""
    created_at: dt.datetime
    confirmed_at: dt.datetime | None
    cancelled_at: dt.datetime | None
    refunded_at: dt.datetime | None
    refund_reason: str | None
    refunded_by_login: str | None
    """Логин Super Admin, аннулировавшего уже подтверждённую регистрацию —
    см. DECISIONS_LOG.md №69."""

    model_config = {"from_attributes": True}


class ManualRegistrationCreateRequest(BaseModel):
    giveaway_id: int
    participant_phone: str
    participant_full_name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(gt=0)
    comment: str | None = None

    @field_validator("participant_full_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Имя участника не может быть пустым")
        return stripped


class PaymentOut(BaseModel):
    id: int
    order_id: str
    participant_id: int
    participant_phone: str
    participant_full_name: str | None
    giveaway_id: int
    giveaway_name: str
    provider: str
    channel: str | None
    amount: int
    quantity: int
    status: str
    created_at: dt.datetime
    confirmed_at: dt.datetime | None
    invoice_no: str | None
    oversold: bool
    amount_mismatch: bool
    amount_mismatch_bank_amount: int | None
    receipt_count: int
    refunded_at: dt.datetime | None
    refund_reason: str | None
    refunded_by_login: str | None
    """Логин Super Admin, аннулировавшего уже оплаченный платёж — см. DECISIONS_LOG.md №69."""

    model_config = {"from_attributes": True}


class RefundRequest(BaseModel):
    """Причина аннулирования уже завершённой покупки — обязательна, попадает в
    AuditLog и хранится на самой покупке (см. DECISIONS_LOG.md №69)."""

    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Причина аннулирования не может быть пустой")
        return stripped


class PaymentReceiptOut(BaseModel):
    id: int
    payment_id: int
    original_filename: str | None
    content_type: str | None
    uploaded_at: dt.datetime

    model_config = {"from_attributes": True}


class BankReconciliationRunOut(BaseModel):
    id: int
    started_at: dt.datetime
    finished_at: dt.datetime | None
    status: str
    candidates_checked: int
    entries_fetched: int | None
    matched_count: int
    mismatch_count: int
    ttl_expired_count: int
    finalize_error_count: int
    error_message: str | None

    model_config = {"from_attributes": True}


class PaymentsCohortBriefOut(BaseModel):
    total_count: int
    total_amount: int
    succeeded_count: int
    succeeded_amount: int
    pending_count: int
    pending_amount: int
    disputed_count: int
    disputed_amount: int

    model_config = {"from_attributes": True}


class PaymentsBriefOut(BaseModel):
    today: PaymentsCohortBriefOut
    yesterday: PaymentsCohortBriefOut

    model_config = {"from_attributes": True}


class BankReconciliationStatusOut(BaseModel):
    runs: list[BankReconciliationRunOut]
    total_runs_24h: int
    failed_runs_24h: int
    last_success_at: dt.datetime | None
    is_stale: bool
    payments_brief: PaymentsBriefOut


class TicketOut(BaseModel):
    id: int
    giveaway_id: int
    giveaway_name: str
    number: int
    full_code: str
    participant_id: int
    participant_phone: str
    participant_full_name: str | None
    source: str
    channel: str | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class PanelUserOut(BaseModel):
    id: int
    login: str
    role: str
    is_blocked: bool
    last_login_at: dt.datetime | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class PanelUserCreateRequest(BaseModel):
    login: str
    password: str = Field(min_length=8)
    role: str


class PanelUserUpdateRequest(BaseModel):
    is_blocked: bool | None = None
    role: str | None = None
    password: str | None = Field(default=None, min_length=8)


class PlatformSettingsOut(BaseModel):
    ignore_phone_verification: bool
    online_status_poll_interval_sec: int
    online_status_poll_max_attempts: int
    online_reservation_ttl_sec: int
    manual_reservation_ttl_sec: int
    support_contacts: dict[str, Any]

    model_config = {"from_attributes": True}


class SupportContactsUpdateRequest(BaseModel):
    support_contacts: dict[str, Any]


class IgnorePhoneVerificationUpdateRequest(BaseModel):
    ignore_phone_verification: bool


class AuditLogOut(BaseModel):
    id: int
    action: str
    actor_type: str
    actor_id: int | None
    actor_label: str
    entity_type: str | None
    entity_id: int | None
    details: dict[str, Any]
    ip_address: str | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class DashboardGiveawayCardOut(BaseModel):
    """Карточка коллекции на Dashboard — см. app/services/dashboard_service.py."""

    id: int
    name: str
    prefix: str
    is_registration_open: bool
    is_locked: bool
    is_closed_forever: bool
    is_archived: bool
    opened_at: dt.datetime | None
    max_tickets: int
    tickets_issued: int
    tickets_reserved: int
    free_tickets_count: int
    revenue_online: int
    revenue_offline: int
    revenue_total: int
    sparkline: list[int]

    model_config = {"from_attributes": True}


class DashboardSalesPointOut(BaseModel):
    period: str
    count: int
    amount: int


class ChannelSalesOut(BaseModel):
    channel: str
    count: int
    amount: int


class DashboardAlertOut(BaseModel):
    """Операционный алерт Dashboard — плоская структура на все типы (см.
    `DashboardAlert` в app/services/dashboard_service.py: какие поля заполнены,
    зависит от `type`, текст алерта собирает фронт)."""

    type: Literal["low_stock", "sales_stalled", "manual_registration_expiring", "bank_mismatch"]
    giveaway_id: int | None = None
    giveaway_name: str | None = None
    free_tickets_count: int | None = None
    max_tickets: int | None = None
    stalled_days: int | None = None
    manual_registration_id: int | None = None
    minutes_until_expiry: int | None = None
    payment_id: int | None = None
    invoice_no: str | None = None
    hours_open: int | None = None

    model_config = {"from_attributes": True}


class DashboardOut(BaseModel):
    participants_count: int
    tickets_issued_count: int
    revenue_online: int
    revenue_offline: int
    revenue_total: int
    giveaways_count: int
    giveaways: list[DashboardGiveawayCardOut]
    sales_trend: list[DashboardSalesPointOut]
    alerts: list[DashboardAlertOut]
    # Средний чек (только по онлайн-платежам — см. report_service.financial_summary).
    average_check: int
    # Разбивка выручки по каналу связи (Telegram/VK) — см. report_service.sales_by_channel.
    revenue_by_channel: list[ChannelSalesOut]
    # Сумма графика "Динамика продаж" (sales_trend) за предыдущие SALES_TREND_DAYS
    # дней — сравнить сам с собой, не открывая «Отчёты» (см. backend/api/dashboard.py).
    sales_trend_prev_total: int
    # Выручка (онлайн + офлайн) сегодня и вчера целиком — для дельты на hero-карточке.
    revenue_today: int
    revenue_yesterday: int
