"""اجرای یک‌باره Worker برای تست زیرساخت."""

from __future__ import annotations

import asyncio
import sys

from workers.bale_worker import BaleWorker
from workers.config import get_worker_settings
from workers.telegram_worker import TelegramWorker


def build_worker():
    settings = get_worker_settings()
    platform = settings.WORKER_PLATFORM.lower().strip()
    common = {
        "account_id": settings.WORKER_ACCOUNT_ID,
        "redis_url": settings.REDIS_URL,
        "database_url": settings.DATABASE_URL,
        "poll_interval_seconds": settings.WORKER_POLL_INTERVAL_SECONDS,
        "log_level": settings.WORKER_LOG_LEVEL,
    }
    if platform == "bale":
        return BaleWorker(**common)
    if platform == "telegram":
        return TelegramWorker(**common)
    raise ValueError(f"Unsupported WORKER_PLATFORM: {settings.WORKER_PLATFORM}")


async def main() -> int:
    worker = build_worker()
    await worker.connect()
    try:
        await worker.run_once()
    finally:
        await worker.disconnect()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
