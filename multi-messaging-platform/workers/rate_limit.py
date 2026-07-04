"""Per-account send rate limiting backed by Redis."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from workers.config import get_worker_settings
from workers.redis_keys import daily_rate_key, delay_key, hourly_rate_key

if TYPE_CHECKING:
    from core_engine.models import Account
    from redis.asyncio import Redis

# TTL شمارنده روزانه — ۴۸ ساعت (پوشش کامل روز جاری + سُرشدن ساعتی timezone).
DAILY_COUNTER_TTL_SECONDS = 172800


async def is_min_delay_active(redis: Redis, account_id: int | str) -> bool:
    """True when the account is still in a mandatory cooldown window."""
    ttl = await redis.ttl(delay_key(account_id))
    return ttl is not None and ttl > 0


async def set_min_delay(redis: Redis, account_id: int | str, delay_seconds: int) -> None:
    if delay_seconds <= 0:
        return
    await redis.set(delay_key(account_id), "1", ex=delay_seconds)


async def hourly_send_count(redis: Redis, account_id: int | str) -> int:
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    value = await redis.get(hourly_rate_key(account_id, hour))
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def is_hourly_cap_reached(
    redis: Redis,
    account_id: int | str,
    hourly_cap: int,
) -> bool:
    if hourly_cap <= 0:
        return False
    return await hourly_send_count(redis, account_id) >= hourly_cap


async def record_successful_send(redis: Redis, account_id: int | str) -> int:
    """Increment hourly counter and return the new count."""
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = hourly_rate_key(account_id, hour)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 7200)
    return int(count)


# ── Warming + daily cap (Phase 4) ────────────────────────────────────────────


def warming_ramp(day: int) -> int:
    """سقف مجاز ارسال روزانه بر اساس روز warming (روز اول = day 0)."""
    if day <= 2:
        return 5  # روز ۱–۳
    if day <= 6:
        return 15  # روز ۴–۷
    if day <= 13:
        return 50  # روز ۸–۱۴
    return 150  # روز ۱۵+


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


async def get_daily_count(redis: Redis, account_id: int | str) -> int:
    """تعداد ارسال موفق امروز (UTC) برای این اکانت."""
    value = await redis.get(daily_rate_key(account_id, _utc_day()))
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def incr_daily_count(redis: Redis, account_id: int | str) -> int:
    """شمارنده روزانه را یک واحد افزایش می‌دهد و مقدار جدید را برمی‌گرداند."""
    key = daily_rate_key(account_id, _utc_day())
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, DAILY_COUNTER_TTL_SECONDS)
    return int(count)


def compute_warming_day(warming_started_at: datetime | None) -> int:
    """روز warming از warming_started_at. NULL → امروز (day=0، امن‌ترین حالت)."""
    if warming_started_at is None:
        return 0
    started = warming_started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    delta_days = (datetime.now(timezone.utc).date() - started.date()).days
    return max(0, delta_days)


def effective_daily_cap(
    warming_day: int,
    policy_daily_cap: int | None,
    setting_cap: int,
) -> int:
    """سقف مؤثر امروز = min(warming_ramp(day), cap).

    cap کف از setting_cap می‌آید؛ اگر policy_daily_cap موجود باشد override می‌کند.
    """
    cap = setting_cap
    if policy_daily_cap is not None:
        cap = policy_daily_cap
    return min(warming_ramp(warming_day), cap)


async def can_send_daily(
    account: "Account",
    redis: Redis,
) -> tuple[bool, str, int, int]:
    """آیا این اکانت امروز مجاز به ارسال است؟

    Returns:
        (allowed, reason_fa, count, cap)
    """
    setting_cap = get_worker_settings().WHATSAPP_DAILY_SEND_CAP

    policy_daily_cap: int | None = None
    try:
        policy = getattr(account, "policy", None)
        if policy is not None:
            policy_daily_cap = policy.daily_cap
    except Exception:
        policy_daily_cap = None

    warming_day = compute_warming_day(getattr(account, "warming_started_at", None))
    count = await get_daily_count(redis, account.id)
    cap = effective_daily_cap(warming_day, policy_daily_cap, setting_cap)

    if count < cap:
        return True, "", count, cap
    return (
        False,
        f"سقف روزانه اکانت پر شد ({count}/{cap}، روز warming={warming_day}).",
        count,
        cap,
    )
