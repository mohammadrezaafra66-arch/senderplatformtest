"""Celery task definitions for background workers."""

import logging
import os
from typing import Any

from celery import Celery
from celery.schedules import crontab, schedule

logger = logging.getLogger(__name__)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("core_engine", broker=redis_url, backend=redis_url)

celery_app.conf.beat_schedule = {
    "push-ready-staged-items-every-5-seconds": {
        "task": "push_ready_staged_items",
        "schedule": schedule(run_every=5),
    },
    "consume-whatsapp-baileys-results-every-10-seconds": {
        "task": "consume_whatsapp_baileys_results",
        "schedule": schedule(run_every=10),
    },
    "consume-whatsapp-session-status-every-10-seconds": {
        "task": "consume_whatsapp_baileys_session_status",
        "schedule": schedule(run_every=10),
    },
    "check-telegram-send-window-every-minute": {
        "task": "check_telegram_send_window",
        "schedule": schedule(run_every=60),
    },
    "refresh-telegram-warmup-caps-daily": {
        "task": "refresh_telegram_warmup_caps",
        "schedule": crontab(hour=0, minute=5),
    },
}


@celery_app.task
def add_numbers(a: int, b: int) -> int:
    return a + b


@celery_app.task(bind=True, max_retries=3, default_retry_delay=1)
def send_message_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a message payload with dry-run / shadow mode handling."""
    from core_engine.services.message_dispatch import dispatch_message

    try:
        return dispatch_message(payload)
    except Exception as exc:
        logger.exception("send_message_task failed (attempt %s)", self.request.retries + 1)
        raise self.retry(exc=exc)


@celery_app.task(name="push_ready_staged_items")
def push_ready_staged_items_task():
    """Periodic task to push READY items from RUNNING campaigns to Redis worker queues."""
    import asyncio

    from core_engine.database import SessionLocal
    from core_engine.services.queue_bridge import push_staged_items_to_worker_queue

    session = SessionLocal()
    try:
        result = asyncio.run(push_staged_items_to_worker_queue(session, batch_size=500))
        return result
    except Exception as exc:
        logger.exception("push_ready_staged_items_task failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()


@celery_app.task(name="consume_whatsapp_baileys_results")
def consume_whatsapp_baileys_results_task():
    """Drain whatsapp:results (Baileys Node workers) into audit_logs."""
    from core_engine.config import get_settings
    from core_engine.services.baileys_results_consumer import consume_whatsapp_results_batch

    if get_settings().WHATSAPP_DELIVERY_MODE.strip().lower() != "baileys":
        return {"processed": 0, "skipped": True}

    try:
        return consume_whatsapp_results_batch()
    except Exception as exc:
        logger.exception("consume_whatsapp_baileys_results_task failed: %s", exc)
        return {"error": str(exc)}


@celery_app.task(name="consume_whatsapp_baileys_session_status")
def consume_whatsapp_baileys_session_status_task():
    """Apply session_invalid events → channel_sessions + account status."""
    from core_engine.config import get_settings
    from core_engine.services.baileys_results_consumer import (
        consume_whatsapp_session_status_batch,
    )

    if get_settings().WHATSAPP_DELIVERY_MODE.strip().lower() != "baileys":
        return {"processed": 0, "skipped": True}

    try:
        return consume_whatsapp_session_status_batch()
    except Exception as exc:
        logger.exception("consume_whatsapp_baileys_session_status_task failed: %s", exc)
        return {"error": str(exc)}
