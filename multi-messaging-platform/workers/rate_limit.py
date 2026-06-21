"""Per-account send rate limiting backed by Redis."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from workers.redis_keys import delay_key, hourly_rate_key

if TYPE_CHECKING:
    from redis.asyncio import Redis


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
