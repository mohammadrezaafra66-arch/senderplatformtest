"""Distributed Rubika in-flight + delayed-retry primitives (Phase 6).

Authoritative counters live in Redis (Lua). Process-local locks are not used.
Crash recovery: per-message keys carry TTL so slots cannot leak forever.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from workers.redis_keys import (
    campaign_account_skip_key,
    campaign_safety_pause_key,
    queue_key,
    rubika_delayed_retry_key,
    rubika_inflight_count_key,
    rubika_inflight_member_key,
    rubika_send_lease_key,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("core_engine.services.campaign_inflight")

DEFAULT_INFLIGHT_TTL_SECONDS = 180
DEFAULT_LEASE_TTL_SECONDS = 120

_RESERVE_SCRIPT = """
local count_key = KEYS[1]
local member_key = KEYS[2]
local max_n = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
if redis.call("EXISTS", member_key) == 1 then
  redis.call("EXPIRE", member_key, ttl)
  return {"ok", "duplicate", tostring(tonumber(redis.call("GET", count_key) or "0"))}
end
local current = tonumber(redis.call("GET", count_key) or "0")
if current >= max_n then
  return {"deny", "max", tostring(current)}
end
current = redis.call("INCR", count_key)
redis.call("EXPIRE", count_key, ttl)
redis.call("SET", member_key, "1", "EX", ttl)
return {"ok", "reserved", tostring(current)}
"""

_RELEASE_SCRIPT = """
local count_key = KEYS[1]
local member_key = KEYS[2]
if redis.call("DEL", member_key) == 0 then
  return 0
end
local current = tonumber(redis.call("GET", count_key) or "0")
if current > 0 then
  redis.call("DECR", count_key)
end
return 1
"""

_LEASE_SCRIPT = """
local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local token = ARGV[2]
local current = redis.call("GET", key)
if current and current ~= token then
  return {"deny", current}
end
redis.call("SET", key, token, "EX", ttl)
return {"ok", token}
"""


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def reserve_account_inflight(
    redis: "Redis",
    account_id: int,
    message_id: int | str,
    *,
    max_in_flight: int,
    ttl_seconds: int = DEFAULT_INFLIGHT_TTL_SECONDS,
) -> bool:
    """Reserve one per-account concurrency slot. False = backpressure."""
    if max_in_flight <= 0:
        return True
    result = await redis.eval(
        _RESERVE_SCRIPT,
        2,
        rubika_inflight_count_key(account_id),
        rubika_inflight_member_key(account_id, message_id),
        int(max_in_flight),
        int(ttl_seconds),
    )
    status = _decode(result[0] if result else "")
    return status == "ok"


async def release_account_inflight(
    redis: "Redis",
    account_id: int,
    message_id: int | str,
) -> None:
    try:
        await redis.eval(
            _RELEASE_SCRIPT,
            2,
            rubika_inflight_count_key(account_id),
            rubika_inflight_member_key(account_id, message_id),
        )
    except Exception:  # noqa: BLE001 — release is best-effort; TTL is backup
        logger.warning(
            "event=campaign_inflight_release_failed account_id=%s message_id=%s",
            account_id,
            message_id,
        )


async def acquire_send_lease(
    redis: "Redis",
    message_id: int | str,
    *,
    token: str,
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
) -> bool:
    result = await redis.eval(
        _LEASE_SCRIPT,
        1,
        rubika_send_lease_key(message_id),
        int(ttl_seconds),
        str(token),
    )
    return _decode(result[0] if result else "") == "ok"


async def release_send_lease(redis: "Redis", message_id: int | str) -> None:
    try:
        await redis.delete(rubika_send_lease_key(message_id))
    except Exception:  # noqa: BLE001
        logger.warning("event=campaign_send_lease_release_failed message_id=%s", message_id)


async def set_account_dispatch_skip(
    redis: "Redis",
    account_id: int,
    *,
    code: str,
    ttl_seconds: int,
) -> None:
    ttl = max(5, int(ttl_seconds))
    await redis.set(campaign_account_skip_key(account_id), code, ex=ttl)


async def get_skipped_account_ids(
    redis: "Redis",
    account_ids: list[int],
) -> dict[int, str]:
    skipped: dict[int, str] = {}
    if not account_ids:
        return skipped
    keys = [campaign_account_skip_key(aid) for aid in account_ids]
    values = await redis.mget(keys)
    for account_id, raw in zip(account_ids, values, strict=False):
        if raw:
            skipped[int(account_id)] = _decode(raw)
    return skipped


async def set_campaign_safety_pause(
    redis: "Redis",
    campaign_id: int,
    *,
    code: str,
    reason: str,
) -> None:
    payload = json.dumps(
        {
            "code": code,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
    )
    await redis.set(campaign_safety_pause_key(campaign_id), payload)


async def get_campaign_safety_pause(
    redis: "Redis",
    campaign_id: int,
) -> dict[str, Any] | None:
    raw = await redis.get(campaign_safety_pause_key(campaign_id))
    if not raw:
        return None
    try:
        data = json.loads(_decode(raw))
    except json.JSONDecodeError:
        return {"code": "PAUSED_SAFETY", "reason": _decode(raw)}
    return data if isinstance(data, dict) else {"code": "PAUSED_SAFETY"}


async def clear_campaign_safety_pause(redis: "Redis", campaign_id: int) -> None:
    await redis.delete(campaign_safety_pause_key(campaign_id))


async def enqueue_delayed_retry(
    redis: "Redis",
    *,
    payload_json: str,
    retry_at_unix: float,
) -> None:
    await redis.zadd(rubika_delayed_retry_key(), {payload_json: float(retry_at_unix)})


async def flush_due_delayed_retries(
    redis: "Redis",
    *,
    now_unix: float,
    limit: int = 100,
) -> int:
    """Move due delayed payloads onto per-account queues. Returns flushed count."""
    key = rubika_delayed_retry_key()
    members = await redis.zrangebyscore(key, min=0, max=now_unix, start=0, num=int(limit))
    flushed = 0
    for raw in members or []:
        text = _decode(raw)
        try:
            data = json.loads(text)
            platform = str(data.get("platform") or "rubika")
            account_id = int(data["account_id"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            await redis.zrem(key, raw)
            continue
        await redis.rpush(queue_key(platform, account_id), text)
        await redis.zrem(key, raw)
        flushed += 1
    if flushed:
        logger.info("event=campaign_delayed_retry_flushed count=%s", flushed)
    return flushed
