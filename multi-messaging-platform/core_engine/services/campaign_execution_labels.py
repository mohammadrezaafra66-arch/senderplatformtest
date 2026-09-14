"""Persian labels for campaign execution blockers (distinct from account runtime status)."""

from __future__ import annotations

from datetime import datetime

EXECUTION_BLOCKER_LABELS_FA: dict[str, str] = {
    "MIN_INTERVAL_ACTIVE": "در انتظار فاصله مجاز ارسال",
    "COOLDOWN_ACTIVE": "اکانت در دوره انتظار (cooldown) است.",
    "HOURLY_CAP_REACHED": "سقف ارسال ساعتی اکانت پر شده است.",
    "DAILY_CAP_REACHED": "سقف ارسال روزانه اکانت پر شده است.",
    "OUTSIDE_SEND_WINDOW": "خارج از بازه زمانی ارسال روبیکا است.",
    "ACCOUNT_THROTTLED": "اکانت در حالت محدودیت موقت (throttled) است.",
    "RUBIKA_CIRCUIT_OPEN": "مدارشکن سراسری روبیکا باز است؛ ارسال متوقف شد.",
    "REDIS_UNAVAILABLE": "سرویس Redis در دسترس نیست؛ ارسال متوقف شد.",
    "NO_WORKER_CONSUMER": "Worker فعال برای این اکانت یافت نشد.",
    "CAMPAIGN_NOT_PREPARED": "کمپین هنوز آماده‌سازی نشده است.",
    "ASSIGNMENT_NOT_MATERIALIZED": "پیام‌ها هنوز به فرستنده تخصیص نیافته‌اند.",
    "NO_EXECUTION_READY_SENDER": "هیچ فرستنده‌ای برای ارسال فوری آماده نیست.",
    "DISPATCH_NOT_READY": "ارسال فوری برای این اکانت ممکن نیست.",
    "ACCOUNT_READY": "اکانت سالم است — ارسال فوری هنوز ممکن نیست.",
    "READY": "آماده ارسال",
}


def execution_blocker_label(code: str | None, *, next_send_at: str | None = None) -> str | None:
    if not code:
        return None
    if code == "MIN_INTERVAL_ACTIVE" and next_send_at:
        try:
            dt = datetime.fromisoformat(next_send_at.replace("Z", "+00:00"))
            when = dt.astimezone().strftime("%H:%M")
            return f"در انتظار فاصله مجاز ارسال — ارسال بعدی: {when}"
        except (TypeError, ValueError):
            pass
    return EXECUTION_BLOCKER_LABELS_FA.get(code, code)


def resolve_account_execution_blocker(
    *,
    eligible_now: bool,
    account_ready_now: bool,
    block_code: str | None,
    assignment_materialized: bool,
    campaign_prepared: bool,
    next_allowed_at: str | None = None,
) -> tuple[str | None, str | None]:
    """Return (execution_blocker_code, execution_blocker_label) for preflight rows."""
    if eligible_now:
        return None, None
    if block_code:
        return block_code, execution_blocker_label(block_code, next_send_at=next_allowed_at)
    if not campaign_prepared:
        return "CAMPAIGN_NOT_PREPARED", execution_blocker_label("CAMPAIGN_NOT_PREPARED")
    if not assignment_materialized:
        return (
            "ASSIGNMENT_NOT_MATERIALIZED",
            execution_blocker_label("ASSIGNMENT_NOT_MATERIALIZED"),
        )
    if account_ready_now:
        return "ACCOUNT_READY", execution_blocker_label("ACCOUNT_READY")
    return block_code, execution_blocker_label(block_code)
