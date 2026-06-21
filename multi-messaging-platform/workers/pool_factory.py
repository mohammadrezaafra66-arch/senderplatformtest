"""Build multi-account worker pools from environment settings."""

from __future__ import annotations

from workers.account_pool import resolve_assigned_account_ids, resolve_pool_index
from workers.config import WorkerSettings, get_worker_settings
from workers.multi_account_worker import MultiAccountWorker
from workers.whatsapp_pool_worker import WhatsAppPoolWorker


def build_pool_worker(settings: WorkerSettings | None = None) -> MultiAccountWorker:
    cfg = settings or get_worker_settings()
    platform = cfg.WORKER_PLATFORM.lower().strip()

    if platform != "whatsapp":
        raise ValueError(
            f"Worker pool mode is only supported for WORKER_PLATFORM=whatsapp, got '{cfg.WORKER_PLATFORM}'."
        )

    assigned_account_ids = resolve_assigned_account_ids(
        account_ids_raw=cfg.WHATSAPP_ACCOUNT_IDS,
        pool_size=cfg.WORKER_POOL_SIZE,
        pool_index=cfg.WORKER_POOL_INDEX,
        fallback_account_id=cfg.WORKER_ACCOUNT_ID,
    )

    resolved_index = resolve_pool_index(
        pool_size=cfg.WORKER_POOL_SIZE,
        explicit_index=cfg.WORKER_POOL_INDEX,
    )

    worker = WhatsAppPoolWorker(
        account_ids=assigned_account_ids,
        redis_url=cfg.REDIS_URL,
        database_url=cfg.DATABASE_URL,
        poll_interval_seconds=cfg.WORKER_POLL_INTERVAL_SECONDS,
        log_level=cfg.WORKER_LOG_LEVEL,
        browser_lock_enabled=cfg.WHATSAPP_POOL_BROWSER_LOCK,
        settings=cfg,
        max_retry_attempts=cfg.WHATSAPP_MAX_RETRY_ATTEMPTS,
        retry_base_delay_seconds=cfg.WHATSAPP_RETRY_BASE_DELAY_SECONDS,
        pool_size=cfg.WORKER_POOL_SIZE,
        pool_index=resolved_index,
    )
    worker.logger.info(
        "whatsapp_pool_started pool_size=%s pool_index=%s assigned_accounts=%s",
        cfg.WORKER_POOL_SIZE,
        resolved_index,
        ",".join(str(account_id) for account_id in assigned_account_ids),
    )
    return worker
