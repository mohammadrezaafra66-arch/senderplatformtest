"""Build platform workers from environment settings."""

from __future__ import annotations

from workers.bale_worker import BaleWorker
from workers.base_worker import BaseWorker
from workers.config import WorkerSettings, get_worker_settings
from workers.rubika_worker import RubikaWorker
from workers.telegram_worker import TelegramWorker
from workers.whatsapp_worker import WhatsAppWorker

_WORKER_CLASSES = {
    "bale": BaleWorker,
    "telegram": TelegramWorker,
    "whatsapp": WhatsAppWorker,
    "rubika": RubikaWorker,
}


def build_worker(settings: WorkerSettings | None = None) -> BaseWorker:
    cfg = settings or get_worker_settings()
    platform = cfg.WORKER_PLATFORM.lower().strip()
    worker_cls = _WORKER_CLASSES.get(platform)
    if worker_cls is None:
        supported = ", ".join(sorted(_WORKER_CLASSES))
        raise ValueError(f"Unsupported WORKER_PLATFORM '{cfg.WORKER_PLATFORM}'. Use: {supported}")

    return worker_cls(
        account_id=cfg.WORKER_ACCOUNT_ID,
        redis_url=cfg.REDIS_URL,
        database_url=cfg.DATABASE_URL,
        poll_interval_seconds=cfg.WORKER_POLL_INTERVAL_SECONDS,
        log_level=cfg.WORKER_LOG_LEVEL,
    )
