"""Build platform workers from environment settings."""

from __future__ import annotations

from workers.bale_worker import BaleWorker
from workers.base_worker import BaseWorker, WorkerExecutionDisabled
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


class WorkerIdentityConflict(ValueError):
    """Worker env identity does not match a valid Account row."""


def _validate_rubika_identity(account_id: int) -> None:
    """Fail-fast before connect/LPOP when Rubika worker identity is invalid."""
    from core_engine.database import SessionLocal
    from core_engine.models import Account, PlatformType

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == int(account_id)).first()
        if account is None:
            raise WorkerIdentityConflict(
                f"WORKER_ACCOUNT_ID={account_id} does not exist — refusing startup."
            )
        if account.platform != PlatformType.RUBIKA:
            raise WorkerIdentityConflict(
                f"WORKER_ACCOUNT_ID={account_id} platform={account.platform.value} "
                "is not RUBIKA — refusing startup."
            )
    finally:
        db.close()


def build_worker(settings: WorkerSettings | None = None) -> BaseWorker:
    cfg = settings or get_worker_settings()
    if not cfg.WORKER_EXECUTION_ENABLED:
        raise WorkerExecutionDisabled(
            "WORKER_EXECUTION_ENABLED=false — refusing worker startup "
            "(zero connect/poll/LPOP/send)."
        )

    platform = cfg.WORKER_PLATFORM.lower().strip()
    worker_cls = _WORKER_CLASSES.get(platform)
    if worker_cls is None:
        supported = ", ".join(sorted(_WORKER_CLASSES))
        raise ValueError(
            f"Unsupported WORKER_PLATFORM '{cfg.WORKER_PLATFORM}'. Use: {supported}"
        )

    account_id = int(cfg.WORKER_ACCOUNT_ID)
    if platform == "rubika":
        _validate_rubika_identity(account_id)

    return worker_cls(
        account_id=account_id,
        redis_url=cfg.REDIS_URL,
        database_url=cfg.DATABASE_URL,
        poll_interval_seconds=cfg.WORKER_POLL_INTERVAL_SECONDS,
        log_level=cfg.WORKER_LOG_LEVEL,
        execution_enabled=bool(cfg.WORKER_EXECUTION_ENABLED),
    )
