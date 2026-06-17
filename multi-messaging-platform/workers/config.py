"""تنظیمات Workerهای مستقل کانال‌ها."""

from functools import lru_cache

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


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
