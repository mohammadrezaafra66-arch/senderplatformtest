"""Rubika Phase 3 — atomic quota reservation (daily / hourly / burst).

Semantics (authoritative for Rubika user_account sends):

1. **Eligibility** (preflight): soft read of counters + delay/cooldown/throttle.
2. **Reservation** (before transport): atomic Lua INCR of daily+hourly counters
   and SET of a reservation token with TTL. At most one concurrent worker can
   consume the final remaining slot.
3. **Commit** (after successful transport): drop reservation marker; set
   minimum-interval delay (with optional jitter); counters stay incremented.
4. **Release** (transport failure / abort): DECR counters (floor 0) and delete
   reservation marker.
5. **Crash recovery**: if the worker dies after reserve, TTL expiry leaves the
   counters incremented (fail-safe — capacity is not oversubscribed). Slot
   recovers naturally at Iran day/hour bucket rollover.

Buckets use Asia/Tehran (same timezone as send windows).

Redis failures raise and MUST be treated as fail-closed by callers.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from core_engine.services.rubika_policy import (
    IRAN_TZ,
    policy_now,
    rubika_day_bucket,
    rubika_hour_bucket,
)
from workers.redis_keys import (
    daily_rate_key,
    delay_key,
    hourly_rate_key,
    rubika_cooldown_meta_key,
    rubika_failure_count_key,
    rubika_reserve_key,
    rubika_throttle_key,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("core_engine.services.rubika_quota")

DAILY_COUNTER_TTL_SECONDS = 172800  # 48h covers Iran day + skew
HOURLY_COUNTER_TTL_SECONDS = 7200
DEFAULT_RESERVE_TTL_SECONDS = 120

# Lua: atomically check delay + caps, then INCR both counters and SET reserve.
# Returns: {ok, code, daily, hourly, ttl_delay} as JSON string via redis protocol
# encoded as multi-bulk that we parse in Python — actually return a table of
# strings for redis-py: [status, code, daily, hourly, delay_ttl]
_RESERVE_SCRIPT = """
local daily_key = KEYS[1]
local hourly_key = KEYS[2]
local delay_key = KEYS[3]
local reserve_key = KEYS[4]
local daily_cap = tonumber(ARGV[1])
local hourly_cap = tonumber(ARGV[2])
local reserve_ttl = tonumber(ARGV[3])
local daily_ttl = tonumber(ARGV[4])
local hourly_ttl = tonumber(ARGV[5])
local token = ARGV[6]

local delay_ttl = redis.call("TTL", delay_key)
if delay_ttl ~= nil and delay_ttl > 0 then
  return {"deny", "MIN_INTERVAL_ACTIVE", tostring(delay_ttl), "0", "0"}
end

local daily = tonumber(redis.call("GET", daily_key) or "0")
local hourly = tonumber(redis.call("GET", hourly_key) or "0")

if daily >= daily_cap then
  return {"deny", "DAILY_CAP_REACHED", "0", tostring(daily), tostring(hourly)}
end
if hourly >= hourly_cap then
  return {"deny", "HOURLY_CAP_REACHED", "0", tostring(daily), tostring(hourly)}
end

daily = redis.call("INCR", daily_key)
if daily == 1 then
  redis.call("EXPIRE", daily_key, daily_ttl)
end
hourly = redis.call("INCR", hourly_key)
if hourly == 1 then
  redis.call("EXPIRE", hourly_key, hourly_ttl)
end

redis.call("SET", reserve_key, "1", "EX", reserve_ttl)
return {"ok", "RESERVED", "0", tostring(daily), tostring(hourly)}
"""

_RELEASE_SCRIPT = """
local daily_key = KEYS[1]
local hourly_key = KEYS[2]
local reserve_key = KEYS[3]
local token = ARGV[1]
if redis.call("EXISTS", reserve_key) == 0 then
  return 0
end
redis.call("DEL", reserve_key)
local daily = tonumber(redis.call("GET", daily_key) or "0")
if daily > 0 then
  redis.call("DECR", daily_key)
end
local hourly = tonumber(redis.call("GET", hourly_key) or "0")
if hourly > 0 then
  redis.call("DECR", hourly_key)
end
return 1
"""


@dataclass(frozen=True, slots=True)
class RubikaQuotaSnapshot:
    sent_today: int
    sent_this_hour: int
    day_bucket: str
    hour_bucket: str
    delay_ttl_seconds: int
    cooldown_until: str | None
    cooldown_reason: str | None
    throttle_active: bool


@dataclass(frozen=True, slots=True)
class RubikaReservation:
    token: str
    account_id: int
    day_bucket: str
    hour_bucket: str
    daily_after: int
    hourly_after: int


@dataclass(frozen=True, slots=True)
class RubikaReserveResult:
    ok: bool
    code: str
    reservation: RubikaReservation | None = None
    retry_after_seconds: int | None = None
    sent_today: int = 0
    sent_this_hour: int = 0


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def read_quota_snapshot(
    redis: "Redis",
    account_id: int,
    *,
    clock: datetime | None = None,
) -> RubikaQuotaSnapshot:
    """Soft-read counters + delay/cooldown/throttle (no mutation)."""
    day = rubika_day_bucket(clock)
    hour = rubika_hour_bucket(clock)
    daily_raw = await redis.get(daily_rate_key(account_id, day))
    hourly_raw = await redis.get(hourly_rate_key(account_id, hour))
    try:
        sent_today = int(daily_raw) if daily_raw is not None else 0
    except (TypeError, ValueError):
        sent_today = 0
    try:
        sent_this_hour = int(hourly_raw) if hourly_raw is not None else 0
    except (TypeError, ValueError):
        sent_this_hour = 0

    delay_ttl = await redis.ttl(delay_key(account_id))
    delay_ttl_seconds = int(delay_ttl) if delay_ttl and delay_ttl > 0 else 0

    cooldown_until = None
    cooldown_reason = None
    meta_raw = await redis.get(rubika_cooldown_meta_key(account_id))
    if meta_raw is not None:
        try:
            meta = json.loads(_decode(meta_raw))
            cooldown_until = meta.get("until")
            cooldown_reason = meta.get("reason")
        except (TypeError, ValueError, json.JSONDecodeError):
            cooldown_until = None
            cooldown_reason = "cooldown"

    # Cooldown meta without TTL still counts if until is in the future.
    if cooldown_until:
        try:
            until_dt = datetime.fromisoformat(cooldown_until)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            if policy_now(clock=clock).astimezone(timezone.utc) >= until_dt.astimezone(
                timezone.utc
            ):
                cooldown_until = None
                cooldown_reason = None
                await redis.delete(rubika_cooldown_meta_key(account_id))
        except ValueError:
            pass

    throttle_ttl = await redis.ttl(rubika_throttle_key(account_id))
    throttle_active = bool(throttle_ttl and throttle_ttl > 0)
    if not throttle_active:
        # Also treat key presence without TTL as active.
        if await redis.exists(rubika_throttle_key(account_id)):
            throttle_active = True

    return RubikaQuotaSnapshot(
        sent_today=sent_today,
        sent_this_hour=sent_this_hour,
        day_bucket=day,
        hour_bucket=hour,
        delay_ttl_seconds=delay_ttl_seconds,
        cooldown_until=cooldown_until,
        cooldown_reason=cooldown_reason,
        throttle_active=throttle_active,
    )


async def is_min_interval_active(redis: "Redis", account_id: int) -> tuple[bool, int]:
    ttl = await redis.ttl(delay_key(account_id))
    if ttl is not None and ttl > 0:
        return True, int(ttl)
    return False, 0


async def reserve_send_quota(
    redis: "Redis",
    account_id: int,
    *,
    daily_cap: int,
    hourly_cap: int,
    reserve_ttl_seconds: int = DEFAULT_RESERVE_TTL_SECONDS,
    clock: datetime | None = None,
    token: str | None = None,
) -> RubikaReserveResult:
    """Atomically reserve one daily+hourly slot. Fail-closed on Redis errors."""
    day = rubika_day_bucket(clock)
    hour = rubika_hour_bucket(clock)
    tok = token or uuid.uuid4().hex
    reserve = rubika_reserve_key(account_id, tok)
    result = await redis.eval(
        _RESERVE_SCRIPT,
        4,
        daily_rate_key(account_id, day),
        hourly_rate_key(account_id, hour),
        delay_key(account_id),
        reserve,
        int(daily_cap),
        int(hourly_cap),
        int(reserve_ttl_seconds),
        DAILY_COUNTER_TTL_SECONDS,
        HOURLY_COUNTER_TTL_SECONDS,
        tok,
    )
    status = _decode(result[0]) if result else "deny"
    code = _decode(result[1]) if result and len(result) > 1 else "REDIS_UNAVAILABLE"
    retry_after = int(_decode(result[2]) or "0") if result and len(result) > 2 else 0
    daily_after = int(_decode(result[3]) or "0") if result and len(result) > 3 else 0
    hourly_after = int(_decode(result[4]) or "0") if result and len(result) > 4 else 0

    if status == "ok":
        return RubikaReserveResult(
            ok=True,
            code="RESERVED",
            reservation=RubikaReservation(
                token=tok,
                account_id=int(account_id),
                day_bucket=day,
                hour_bucket=hour,
                daily_after=daily_after,
                hourly_after=hourly_after,
            ),
            sent_today=daily_after,
            sent_this_hour=hourly_after,
        )

    return RubikaReserveResult(
        ok=False,
        code=code or "REDIS_UNAVAILABLE",
        retry_after_seconds=retry_after if retry_after > 0 else None,
        sent_today=daily_after,
        sent_this_hour=hourly_after,
    )


async def commit_reservation(
    redis: "Redis",
    reservation: RubikaReservation,
    *,
    min_interval_seconds: int,
) -> None:
    """Finalize a successful send: clear reserve marker, set min-interval delay.

    Counters were already incremented at reserve time.
    """
    await redis.delete(rubika_reserve_key(reservation.account_id, reservation.token))
    if min_interval_seconds > 0:
        await redis.set(
            delay_key(reservation.account_id),
            "1",
            ex=int(min_interval_seconds),
        )


async def release_reservation(
    redis: "Redis",
    reservation: RubikaReservation,
) -> bool:
    """Undo a failed/aborted reservation (DECR counters if marker still present)."""
    released = await redis.eval(
        _RELEASE_SCRIPT,
        3,
        daily_rate_key(reservation.account_id, reservation.day_bucket),
        hourly_rate_key(reservation.account_id, reservation.hour_bucket),
        rubika_reserve_key(reservation.account_id, reservation.token),
        reservation.token,
    )
    return bool(released)


async def enter_cooldown(
    redis: "Redis",
    account_id: int,
    *,
    seconds: int,
    reason: str,
    clock: datetime | None = None,
) -> str:
    """Start an explicit cooldown (separate from min-interval delay)."""
    now = policy_now(clock=clock).astimezone(timezone.utc)
    until = now.timestamp() + max(1, int(seconds))
    until_iso = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
    meta = json.dumps(
        {
            "account_id": int(account_id),
            "reason": reason,
            "started_at": now.isoformat(),
            "until": until_iso,
        }
    )
    await redis.set(rubika_cooldown_meta_key(account_id), meta, ex=max(1, int(seconds)))
    await redis.set(delay_key(account_id), "1", ex=max(1, int(seconds)))
    logger.warning(
        "event=rubika_cooldown_entered account_id=%s reason=%s until=%s",
        account_id,
        reason,
        until_iso,
    )
    return until_iso


async def enter_throttle(
    redis: "Redis",
    account_id: int,
    *,
    seconds: int,
    reason: str = "transport_failures",
) -> None:
    await redis.set(rubika_throttle_key(account_id), reason, ex=max(1, int(seconds)))
    logger.warning(
        "event=rubika_throttle_entered account_id=%s reason=%s seconds=%s",
        account_id,
        reason,
        seconds,
    )


async def record_send_failure(
    redis: "Redis",
    account_id: int,
    *,
    threshold: int = 3,
    cooldown_seconds: int = 300,
    throttle_seconds: int = 600,
    clock: datetime | None = None,
) -> str | None:
    """Foundation for failure-driven throttling.

    Returns action taken: None | 'cooldown' | 'throttle'.
    Single transient failure does not suspend.
    """
    key = rubika_failure_count_key(account_id)
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, 3600)
    if count >= threshold * 2:
        await enter_throttle(redis, account_id, seconds=throttle_seconds, reason="repeated_failures")
        return "throttle"
    if count >= threshold:
        await enter_cooldown(
            redis,
            account_id,
            seconds=cooldown_seconds,
            reason="repeated_transport_failures",
            clock=clock,
        )
        return "cooldown"
    return None


async def clear_failure_count(redis: "Redis", account_id: int) -> None:
    await redis.delete(rubika_failure_count_key(account_id))
