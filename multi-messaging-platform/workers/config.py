"""تنظیمات Workerهای مستقل کانال‌ها."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql://mmp_user:mmp_pass@localhost:5432/mmp_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    WORKER_PLATFORM: str = "bale"
    WORKER_ACCOUNT_ID: int = 1
    WORKER_POLL_INTERVAL_SECONDS: int = 5
    WORKER_DEFAULT_DELAY_SECONDS: int = 3
    WORKER_DEFAULT_HOURLY_CAP: int = 100
    WORKER_LOG_LEVEL: str = "INFO"

    DRY_RUN: bool = False
    SHADOW_MODE: bool = False
    SHADOW_PHONE_NUMBER: str = ""
    REAL_MESSAGE_SENDING_ENABLED: bool = False
    CHANNEL_CONNECTORS_ENABLED: bool = False

    BALE_API_BASE_URL: str = "https://tapi.bale.ai"
    BALE_API_TIMEOUT_SECONDS: float = 30.0

    TELEGRAM_API_BASE_URL: str = "https://api.telegram.org"
    TELEGRAM_API_TIMEOUT_SECONDS: float = 30.0

    RUBIKA_API_BASE_URL: str = "https://botapi.rubika.ir/v3"
    RUBIKA_API_TIMEOUT_SECONDS: float = 30.0

    WHATSAPP_API_BASE_URL: str = "https://graph.facebook.com/v21.0"
    WHATSAPP_API_TIMEOUT_SECONDS: float = 30.0

    # WhatsApp delivery: web (Playwright + QR session) or cloud_api (Meta Graph API).
    WHATSAPP_DELIVERY_MODE: str = "web"
    WHATSAPP_WEB_PROFILE_ROOT: str = "storage/browser_profiles/whatsapp"
    WHATSAPP_WEB_HEADLESS: bool = True
    WHATSAPP_WEB_SEND_TIMEOUT_SECONDS: float = 90.0

    # Multi-account WhatsApp worker pool (WA-4).
    WHATSAPP_ACCOUNT_IDS: str = ""
    WORKER_POOL_SIZE: int = 1
    WORKER_POOL_INDEX: int = -1
    WHATSAPP_POOL_BROWSER_LOCK: bool = True

    # WA-5 — retry, rate limit, distributed lock, heartbeat.
    WHATSAPP_MAX_RETRY_ATTEMPTS: int = 3
    WHATSAPP_RETRY_BASE_DELAY_SECONDS: float = 5.0
    WHATSAPP_MIN_SEND_DELAY_SECONDS: int = 3
    WHATSAPP_HOURLY_SEND_CAP: int = 100
    WHATSAPP_DISTRIBUTED_LOCK_ENABLED: bool = True
    WHATSAPP_DISTRIBUTED_LOCK_TTL_SECONDS: int = 120
    WORKER_HEARTBEAT_INTERVAL_SECONDS: int = 15
    WORKER_HEARTBEAT_TTL_SECONDS: int = 45

    # Telegram MTProto (Telethon)
    TELEGRAM_DELIVERY_MODE: str = "bot_api"
    TELEGRAM_API_ID: str = ""
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_MTPROTO_SESSION_DIR: str = "storage/telegram_mtproto_sessions"
    TELEGRAM_ENABLE_MTPROTO: bool = False
    TELEGRAM_MIN_SEND_DELAY_SECONDS: int = 15
    TELEGRAM_MAX_SEND_DELAY_SECONDS: int = 45
    TELEGRAM_WARMUP_DAYS: int = 14
    TELEGRAM_WARMUP_START_CAP: int = 10
    TELEGRAM_WARMUP_FINAL_CAP: int = 80

    @field_validator("WHATSAPP_DELIVERY_MODE", mode="before")
    @classmethod
    def normalize_whatsapp_delivery_mode(cls, value: object) -> str:
        mode = str(value or "web").strip().lower()
        if mode not in {"web", "cloud_api", "evolution"}:
            raise ValueError(
                "WHATSAPP_DELIVERY_MODE must be 'web', 'cloud_api' or 'evolution'."
            )
        return mode

    @field_validator("SHADOW_PHONE_NUMBER", mode="before")
    @classmethod
    def strip_shadow_phone(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("WORKER_POOL_SIZE")
    @classmethod
    def validate_worker_pool_size(cls, value: int) -> int:
        if value < 1:
            raise ValueError("WORKER_POOL_SIZE must be >= 1.")
        return value


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
