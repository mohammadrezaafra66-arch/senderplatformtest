"""وظایف پس‌زمینه Celery."""

import logging
import os
from typing import Any

from celery import Celery

logger = logging.getLogger(__name__)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("core_engine", broker=redis_url, backend=redis_url)


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

