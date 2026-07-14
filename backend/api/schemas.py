"""Pydantic-схемы запросов/ответов API панели."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


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
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class ParticipantUpdateRequest(BaseModel):
    full_name: str | None = None


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


class ManualRegistrationOut(BaseModel):
    id: int
    participant_id: int
    giveaway_id: int
    quantity: int
    status: str
    operator_id: int
    comment: str | None
    created_at: dt.datetime
    confirmed_at: dt.datetime | None
    cancelled_at: dt.datetime | None

    model_config = {"from_attributes": True}


class ManualRegistrationCreateRequest(BaseModel):
    giveaway_id: int
    participant_phone: str
    quantity: int = Field(gt=0)
    comment: str | None = None


class PaymentOut(BaseModel):
    id: int
    order_id: str
    participant_id: int
    giveaway_id: int
    provider: str
    amount: int
    quantity: int
    status: str
    created_at: dt.datetime
    confirmed_at: dt.datetime | None

    model_config = {"from_attributes": True}


class TicketOut(BaseModel):
    id: int
    giveaway_id: int
    number: int
    full_code: str
    participant_id: int
    source: str
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
    payment_provider_override: str | None
    ignore_phone_verification: bool
    online_status_poll_interval_sec: int
    online_status_poll_max_attempts: int
    online_reservation_ttl_sec: int
    manual_reservation_ttl_sec: int
    support_contacts: dict[str, Any]

    model_config = {"from_attributes": True}


class SupportContactsUpdateRequest(BaseModel):
    support_contacts: dict[str, Any]


class PaymentProviderUpdateRequest(BaseModel):
    payment_provider_override: str | None


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


class DashboardOut(BaseModel):
    participants_count: int
    tickets_issued_count: int
    revenue_online: int
    revenue_offline: int
    revenue_total: int
    giveaways_count: int
