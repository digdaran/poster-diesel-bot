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

    payment_provider: str = Field(default="requisites_qr", alias="PAYMENT_PROVIDER")

    # ---- requisites_qr: реквизиты получателя для статического QR (ГОСТ Р 56042-2014,
    # ST00012) — единственный платёжный провайдер (интернет-эквайринг удалён по
    # прямому запросу заказчика, см. DECISIONS.md).
    requisites_recipient_name: str = Field(default="", alias="REQUISITES_RECIPIENT_NAME")
    requisites_recipient_inn: str = Field(default="", alias="REQUISITES_RECIPIENT_INN")
    requisites_recipient_kpp: str = Field(default="", alias="REQUISITES_RECIPIENT_KPP")
    requisites_personal_acc: str = Field(default="", alias="REQUISITES_PERSONAL_ACC")
    requisites_bank_name: str = Field(default="", alias="REQUISITES_BANK_NAME")
    requisites_bic: str = Field(default="", alias="REQUISITES_BIC")
    requisites_corr_acc: str = Field(default="", alias="REQUISITES_CORR_ACC")
    requisites_vat_rate_percent: int = Field(default=20, alias="REQUISITES_VAT_RATE_PERCENT")
    requisites_invoice_ttl_days: int = Field(default=14, alias="REQUISITES_INVOICE_TTL_DAYS")

    # Лимит суммарного количества экземпляров во всех текущих PENDING-покупках
    # участника (платежи + ручные регистрации, глобально по всем розыгрышам) —
    # по прямому запросу заказчика заменяет прежнее бинарное правило "не более
    # одной активной покупки" (см. DECISIONS.md). 20 по умолчанию — вдвое больше
    # максимального варианта количества за одну покупку (QUANTITY_OPTIONS в
    # channels/*/state.py), оставляет запас для второй покупки, защищая при этом
    # от накопления номерков через много параллельных неоплаченных счетов.
    max_pending_tickets_per_participant: int = Field(
        default=20, alias="MAX_PENDING_TICKETS_PER_PARTICIPANT"
    )

    # ---- Сверка платежей через выписку Т-Банк (T-API) — по документации, без
    # реального прогона (нет тестовых доступов Т-Бизнес), см. DECISIONS.md.
    tbank_statement_api_base: str = Field(
        default="https://business.tbank.ru/openapi/api/v1", alias="TBANK_STATEMENT_API_BASE"
    )
    tbank_statement_api_token: str = Field(default="", alias="TBANK_STATEMENT_API_TOKEN")
    tbank_statement_account_number: str = Field(default="", alias="TBANK_STATEMENT_ACCOUNT_NUMBER")
    # По умолчанию >= requisites_invoice_ttl_days: иначе неоплаченный счёт,
    # успевший выпасть из окна выписки до истечения TTL, никогда не был бы
    # сопоставлен, даже если оплата фактически прошла (см. DECISIONS.md).
    bank_statement_lookback_days: int = Field(default=14, alias="BANK_STATEMENT_LOOKBACK_DAYS")

    # Квитанции, присланные участниками (не распознаются, только хранятся для
    # просмотра в панели) — тот же паттерн bind-mount в ./data/, что и для БД
    # (см. DECISIONS_LOG.md №28).
    receipts_dir: str = Field(default="./data/receipts", alias="RECEIPTS_DIR")

    # Цифровые постеры розыгрышей, загружаемые через веб-админку (см. DECISIONS_LOG.md
    # №46) — тот же паттерн bind-mount в ./data/, что и для квитанций выше.
    poster_dir: str = Field(default="./data/posters", alias="POSTER_DIR")

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

    @property
    def receipts_path(self) -> Path:
        path = Path(self.receipts_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def poster_path(self) -> Path:
        path = Path(self.poster_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
