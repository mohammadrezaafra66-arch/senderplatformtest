"""ابزارهای لاگ JSON-friendly برای Workerها."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "worker": getattr(record, "worker", record.name),
            "platform": getattr(record, "platform", None),
            "account_id": getattr(record, "account_id", None),
            "message_id": getattr(record, "message_id", None),
            "campaign_id": getattr(record, "campaign_id", None),
            "event": getattr(record, "event", record.getMessage()),
            "status": getattr(record, "status", None),
            "error_code": getattr(record, "error_code", None),
            "error_message": getattr(record, "error_message", None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_worker_logger(
    name: str,
    *,
    platform: str | None = None,
    account_id: int | str | None = None,
    level: str = "INFO",
) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level.upper())
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def log_worker_event(
    logger: logging.Logger,
    *,
    event: str,
    status: str | None = None,
    message_id: int | str | None = None,
    campaign_id: int | str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    platform: str | None = None,
    account_id: int | str | None = None,
    level: int = logging.INFO,
) -> None:
    logger.log(
        level,
        event,
        extra={
            "worker": logger.name,
            "platform": platform,
            "account_id": account_id,
            "event": event,
            "status": status,
            "message_id": message_id,
            "campaign_id": campaign_id,
            "error_code": error_code,
            "error_message": error_message,
        },
    )
