"""وظایف پس‌زمینه Celery."""

import logging
import os
from typing import Any

from celery import Celery
from celery.schedules import schedule

logger = logging.getLogger(__name__)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("core_engine", broker=redis_url, backend=redis_url)

celery_app.conf.beat_schedule = {
    "push-ready-staged-items-every-5-seconds": {
        "task": "push_ready_staged_items",
        "schedule": schedule(run_every=5),
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

