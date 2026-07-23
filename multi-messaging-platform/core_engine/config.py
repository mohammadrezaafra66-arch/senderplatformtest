"""تنظیمات و متغیرهای محیطی پروژه."""

import os
from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


DRY_RUN = _env_bool("DRY_RUN")
SHADOW_MODE = _env_bool("SHADOW_MODE")
SHADOW_PHONE_NUMBER = os.getenv("SHADOW_PHONE_NUMBER", "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql://mmp_user:mmp_pass@localhost:5432/mmp_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = ""

    SESSION_SECRET: str = ""

    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: int = 30
    OPENAI_MAX_RETRIES: int = 3

    PRICING_API_URL: str = "http://192.168.170.10:3000/pricing/amin-hozoor-board"
    PRICING_CACHE_TTL_SECONDS: int = 300
    PRICING_ENABLE_SCRAPER_FALLBACK: bool = True
    PRICING_SCRAPER_MIN_ITEMS: int = 1
    PRICING_ENABLE_PLAYWRIGHT_FALLBACK: bool = True
    PRICING_PLAYWRIGHT_HEADLESS: bool = True
    PRICING_PLAYWRIGHT_TIMEOUT_MS: int = 30000
    PRICING_PLAYWRIGHT_PROFILE_DIR: str = "storage/browser_profiles/pricing_scraper"
    PRICING_PLAYWRIGHT_MAX_PAGES: int = 20
    PRICING_PLAYWRIGHT_PAGE_WAIT_MS: int = 1500

    # Phase 4 safety gates — defaults must remain dry-run safe.
    REAL_QUEUE_PUSH_ENABLED: bool = False
    REAL_MESSAGE_SENDING_ENABLED: bool = False
    WORKER_EXECUTION_ENABLED: bool = False
    CHANNEL_CONNECTORS_ENABLED: bool = False
    PHASE_4_DEBUG_MODE: bool = True

    # Phase 6 — dry-run and shadow dispatch modes.
    DRY_RUN: bool = False
    SHADOW_MODE: bool = False
    SHADOW_PHONE_NUMBER: str = ""

    WHATSAPP_WEB_PROFILE_ROOT: str = "storage/browser_profiles/whatsapp"
    WHATSAPP_DELIVERY_MODE: str = "web"

    # Evolution API (WhatsApp) integration.
    EVOLUTION_API_URL: str = "http://localhost:8080"
    EVOLUTION_API_KEY: str = ""
    EVOLUTION_WEBHOOK_URL: str = ""
    WHATSAPP_EVOLUTION_REQUIRE_PROXY: bool = False

    # Phase 9.2 — explicit API gate for live operational test sends (default off).
    OPS_LIVE_SEND_API_ENABLED: bool = False

    # Phase 9 dev — route WhatsApp Web live API test sends to Redis worker queue
    # (use with whatsapp_worker_pool_windows.ps1 on the host).
    WHATSAPP_OPS_SEND_VIA_WORKER_QUEUE: bool = False

    # Node.js whatsapp-service mini-API (Baileys admin + warmup).
    WHATSAPP_SERVICE_URL: str = "http://localhost:3000"
    WHATSAPP_SERVICE_API_KEY: str = ""

    # Emergency stop for all WhatsApp Web sends (UI, worker, scripts). Requires process restart
    # to take effect in long-running workers unless Redis kill switch is also used.
    WHATSAPP_SENDING_DISABLED: bool = False

    # Telegram MTProto safety gates
    TELEGRAM_MTPROTO_ENABLED: bool = False
    TELEGRAM_SEND_WINDOW_START_HOUR: int = 9
    TELEGRAM_SEND_WINDOW_END_HOUR: int = 21

    @field_validator("SESSION_SECRET")
    @classmethod
    def validate_session_secret(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError(
                "SESSION_SECRET environment variable must be set for session encryption."
            )
        try:
            Fernet(stripped.encode("utf-8"))
        except Exception as exc:
            raise ValueError("SESSION_SECRET must be a valid Fernet key.") from exc
        return stripped


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_session_secret() -> bytes:
    """Return the configured Fernet key bytes or raise if misconfigured."""
    return get_settings().SESSION_SECRET.encode("utf-8")
