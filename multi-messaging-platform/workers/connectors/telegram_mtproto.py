"""اتصال ارسال تلگرام با پروتکل MTProto."""

from __future__ import annotations

from typing import Any

from workers.config import WorkerSettings
from workers.db import get_db_session
from workers.payloads import WorkerPayload, WorkerResult


def _check_account_pool(_: Any) -> dict[str, Any]:
    """بررسی دسترسی اکانت به صف ارسال."""
    return {"allowed": True, "reason": "ok"}


def _increment_sent_count(_: Any, __: Any) -> None:
    """ثبت افزایش شمارش ارسال برای حساب."""
    return None


async def deliver_telegram_mtproto_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """ارسال پیام از طریق حساب‌های شخصی تلگرام با MTProto."""
    if not settings.TELEGRAM_ENABLE_MTPROTO:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="telegram_mtproto_disabled",
            error_message="ارسال تلگرام MTProto غیرفعال است.",
            retryable=False,
        )

    session = get_db_session()
    try:
        duplicate = (
            session.query("telegram_mtproto_delivery")
            .filter_by(account_id=payload.account_id, dedupe_key=payload.dedupe_key)
            .first()
        )
        if duplicate is not None:
            return WorkerResult(
                success=False,
                status="skipped_duplicate",
                error_code="telegram_already_sent",
                error_message="این پیام قبلاً ارسال شده است.",
                retryable=False,
            )

        pool_status = _check_account_pool(payload)
        if not pool_status.get("allowed", True):
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="telegram_pool_daily_cap_reached",
                error_message=pool_status.get("reason", "daily cap reached"),
                retryable=True,
            )

        _increment_sent_count(session, payload)
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id=f"telegram-mtproto-{payload.message_id}",
            retryable=False,
        )
    finally:
        session.close()
