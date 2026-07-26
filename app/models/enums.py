"""Перечисления, используемые в моделях (соответствуют глоссарию и п.6.2 ТЗ)."""

from __future__ import annotations

import enum


class ChannelType(str, enum.Enum):
    TELEGRAM = "telegram"
    VK = "vk"
    MAX = "max"


class TicketPoolStatus(str, enum.Enum):
    FREE = "free"
    RESERVED = "reserved"
    ISSUED = "issued"


class TicketSource(str, enum.Enum):
    ONLINE = "online"
    MANUAL = "manual"


class PaymentProviderType(str, enum.Enum):
    TBANK = "tbank"
    VTB = "vtb"
    MOCK = "mock"
    # QR по банковским реквизитам (ГОСТ Р 56042-2014, ST00012) — активный провайдер
    # по умолчанию в этой версии, см. DECISIONS.md. TBANK/VTB (интернет-эквайринг)
    # остаются реализованными, но не используются по продуктовому решению.
    REQUISITES_QR = "requisites_qr"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ManualRegistrationStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class PanelUserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ADMINISTRATOR = "administrator"
    OPERATOR = "operator"


class AuditActorType(str, enum.Enum):
    PANEL_USER = "panel_user"
    PARTICIPANT = "participant"
    SYSTEM = "system"
    BANK = "bank"


class BroadcastStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class BankReconciliationRunStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    FETCH_FAILED = "FETCH_FAILED"
