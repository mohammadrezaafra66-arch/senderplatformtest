"""اتصال دیتابیس و توابع placeholder برای Workerها."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from core_engine.database import SessionLocal
from workers.logging_utils import log_worker_event as _emit_worker_log

logger = logging.getLogger("workers.db")


def get_db_session() -> Session:
    return SessionLocal()


def log_worker_event(
    *,
    event: str,
    platform: str | None = None,
    account_id: int | str | None = None,
    message_id: int | str | None = None,
    campaign_id: int | str | None = None,
    status: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """ثبت رویداد Worker — فعلاً فقط log؛ در مراحل بعد به audit_events متصل می‌شود."""
    _emit_worker_log(
        logger,
        event=event,
        status=status,
        message_id=message_id,
        campaign_id=campaign_id,
        error_code=error_code,
        error_message=error_message,
    )
    if payload:
        logger.debug("worker_event_payload=%s", payload)


def update_message_attempt_result(
    *,
    message_id: int | str,
    attempt_no: int,
    status: str,
    platform_message_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    db: Session | None = None,
) -> None:
    """به‌روزرسانی نتیجه تلاش ارسال — TODO: پیاده‌سازی کامل در مرحله بعد."""
    log_worker_event(
        event="message_attempt_result_placeholder",
        message_id=message_id,
        status=status,
        error_code=error_code,
        error_message=error_message,
        payload={
            "attempt_no": attempt_no,
            "platform_message_id": platform_message_id,
        },
    )
    if db is not None:
        db.close()
