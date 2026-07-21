"""Конфигурация приложения из переменных окружения (.env).

Единый источник настроек для всех процессов (backend, channel-telegram, channel-vk),
переиспользующих общий пакет app/.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    database_path: str = Field(default="./data/raffle.db", alias="DATABASE_PATH")
    db_busy_timeout_ms: int = Field(default=5000, alias="DB_BUSY_TIMEOUT_MS")

    jwt_secret: str = Field(default="dev-insecure-secret-change-me", alias="JWT_SECRET")
    jwt_access_ttl_min: int = Field(default=15, alias="JWT_ACCESS_TTL_MIN")
    jwt_refresh_ttl_days: int = Field(default=30, alias="JWT_REFRESH_TTL_DAYS")

    superadmin_login: str = Field(default="admin", alias="SUPERADMIN_LOGIN")
    superadmin_password: str = Field(default="", alias="SUPERADMIN_PASSWORD")

    payment_provider: str = Field(default="mock", alias="PAYMENT_PROVIDER")
    tbank_terminal_key: str = Field(default="", alias="TBANK_TERMINAL_KEY")
    tbank_secret_key: str = Field(default="", alias="TBANK_SECRET_KEY")
    tbank_api_base: str = Field(default="https://securepay.tinkoff.ru/v2", alias="TBANK_API_BASE")
    # Параметры фискального чека (Receipt), передаваемого в каждом Init — банк требует
    # его для формирования чека по 54-ФЗ (см. DECISIONS.md). Значения по умолчанию —
    # заглушка; заказчик обязан выставить реальные (система налогообложения, ставка НДС)
    # согласно своей регистрации в ФНС/личном кабинете эквайринга перед продакшеном.
    tbank_receipt_taxation: str = Field(default="usn_income", alias="TBANK_RECEIPT_TAXATION")
    tbank_receipt_tax: str = Field(default="none", alias="TBANK_RECEIPT_TAX")
    tbank_receipt_payment_method: str = Field(
        default="full_payment", alias="TBANK_RECEIPT_PAYMENT_METHOD"
    )
    tbank_receipt_payment_object: str = Field(
        default="commodity", alias="TBANK_RECEIPT_PAYMENT_OBJECT"
    )
    vtb_merchant_id: str = Field(default="", alias="VTB_MERCHANT_ID")
    vtb_secret_key: str = Field(default="", alias="VTB_SECRET_KEY")
    vtb_api_base: str = Field(default="https://api.vtb.ru/acquiring", alias="VTB_API_BASE")

    online_status_poll_interval_sec: int = Field(
        default=60, alias="ONLINE_STATUS_POLL_INTERVAL_SEC"
    )
    online_status_poll_max_attempts: int = Field(
        default=10, alias="ONLINE_STATUS_POLL_MAX_ATTEMPTS"
    )
    online_reservation_ttl_sec: int = Field(default=600, alias="ONLINE_RESERVATION_TTL_SEC")
    manual_reservation_ttl_sec: int = Field(default=3600, alias="MANUAL_RESERVATION_TTL_SEC")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_proxy_url: str = Field(default="", alias="TELEGRAM_PROXY_URL")

    vk_group_token: str = Field(default="", alias="VK_GROUP_TOKEN")
    vk_group_id: int | None = Field(default=None, alias="VK_GROUP_ID")

    panel_domain: str = Field(default="localhost", alias="PANEL_DOMAIN")
    panel_ip_whitelist: str = Field(default="127.0.0.1", alias="PANEL_IP_WHITELIST")

    backup_dir: str = Field(default="./backups", alias="BACKUP_DIR")
    backup_retention_days: int = Field(default=14, alias="BACKUP_RETENTION_DAYS")

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL для файловой БД. Используйте отдельный in-memory URL в тестах."""
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
