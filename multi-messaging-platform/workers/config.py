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

    # روبیکا v2 — حالت ارسال: bot_api (موجود، دست‌نخورده) یا user_account (rubpy، جدید).
    RUBIKA_DELIVERY_MODE: str = "bot_api"
    RUBIKA_USER_ACCOUNT_ENABLED: bool = False
    RUBIKA_CONVERSATION_MODE_ENABLED: bool = False
    RUBIKA_SESSION_DIR: str = "storage/rubika_sessions"
    RUBIKA_MIN_SEND_DELAY_SECONDS: int = 5
    RUBIKA_MAX_SEND_DELAY_SECONDS: int = 15
    RUBIKA_HOURLY_SEND_CAP: int = 50
    RUBIKA_DAY_PHASE_START_HOUR: int = 8
    RUBIKA_DAY_PHASE_END_HOUR: int = 22
    RUBIKA_LISTENER_ACCOUNT_ID: int = 0
    RUBIKA_STATUS_ACCOUNT_ID: int = 0
    RUBIKA_STATUS_LIKE_DELAY_MIN_SECONDS: int = 5
    RUBIKA_STATUS_LIKE_DELAY_MAX_SECONDS: int = 30
    RUBIKA_STATUS_HOURLY_LIKE_CAP: int = 30
    RUBIKA_STATUS_DAILY_COMMENT_CAP: int = 10

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

    @field_validator("WHATSAPP_DELIVERY_MODE", mode="before")
    @classmethod
    def normalize_whatsapp_delivery_mode(cls, value: object) -> str:
        mode = str(value or "web").strip().lower()
        if mode not in {"web", "cloud_api", "evolution"}:
            raise ValueError(
                "WHATSAPP_DELIVERY_MODE must be 'web', 'cloud_api' or 'evolution'."
            )
        return mode

    @field_validator("RUBIKA_DELIVERY_MODE", mode="before")
    @classmethod
    def normalize_rubika_delivery_mode(cls, value: object) -> str:
        mode = str(value or "bot_api").strip().lower()
        if mode not in {"bot_api", "user_account"}:
            raise ValueError("RUBIKA_DELIVERY_MODE must be 'bot_api' or 'user_account'.")
        return mode

    @field_validator("RUBIKA_DAY_PHASE_START_HOUR", "RUBIKA_DAY_PHASE_END_HOUR")
    @classmethod
    def validate_rubika_phase_hour(cls, value: int) -> int:
        if not 0 <= value <= 24:
            raise ValueError("RUBIKA day-phase hours must be between 0 and 24.")
        return value

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
