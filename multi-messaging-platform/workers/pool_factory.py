"""Build multi-account worker pools from environment settings."""

from __future__ import annotations

from workers.account_pool import parse_account_id_list, resolve_assigned_account_ids, resolve_pool_index
from workers.base_worker import WorkerExecutionDisabled
from workers.config import WorkerSettings, get_worker_settings
from workers.multi_account_worker import MultiAccountWorker
from workers.rubika_pool_worker import RubikaPoolWorker, discover_rubika_eligible_account_ids
from workers.whatsapp_pool_worker import WhatsAppPoolWorker


def build_pool_worker(settings: WorkerSettings | None = None) -> MultiAccountWorker:
    cfg = settings or get_worker_settings()
    if not cfg.WORKER_EXECUTION_ENABLED:
        raise WorkerExecutionDisabled(
            "WORKER_EXECUTION_ENABLED=false — refusing pool worker startup."
        )

    platform = cfg.WORKER_PLATFORM.lower().strip()

    if platform == "whatsapp":
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
            execution_enabled=bool(cfg.WORKER_EXECUTION_ENABLED),
        )
        worker.logger.info(
            "whatsapp_pool_started pool_size=%s pool_index=%s assigned_accounts=%s",
            cfg.WORKER_POOL_SIZE,
            resolved_index,
            ",".join(str(account_id) for account_id in assigned_account_ids),
        )
        return worker

    if platform == "rubika":
        explicit = parse_account_id_list(getattr(cfg, "RUBIKA_ACCOUNT_IDS", "") or "")
        account_ids = discover_rubika_eligible_account_ids(
            explicit_ids=explicit or None
        )
        worker = RubikaPoolWorker(
            account_ids=account_ids,
            redis_url=cfg.REDIS_URL,
            database_url=cfg.DATABASE_URL,
            poll_interval_seconds=cfg.WORKER_POLL_INTERVAL_SECONDS,
            log_level=cfg.WORKER_LOG_LEVEL,
            settings=cfg,
            max_retry_attempts=3,
            retry_base_delay_seconds=5.0,
            execution_enabled=bool(cfg.WORKER_EXECUTION_ENABLED),
            account_refresh_interval_seconds=int(
                getattr(cfg, "RUBIKA_ACCOUNT_REFRESH_INTERVAL_SECONDS", 60) or 60
            ),
            heartbeat_interval_seconds=int(cfg.WORKER_HEARTBEAT_INTERVAL_SECONDS),
            heartbeat_ttl_seconds=int(cfg.WORKER_HEARTBEAT_TTL_SECONDS),
        )
        worker.logger.info(
            "rubika_pool_started assigned_accounts=%s",
            ",".join(str(account_id) for account_id in account_ids) or "(dynamic)",
        )
        return worker

    raise ValueError(
        f"Worker pool mode supports WORKER_PLATFORM=whatsapp|rubika, got '{cfg.WORKER_PLATFORM}'."
    )
