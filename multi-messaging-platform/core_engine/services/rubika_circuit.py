"""Rubika Phase 4 — system circuit breaker (CLOSED / OPEN / HALF_OPEN).

Represents SYSTEM health across multiple accounts — not a single bad session.
All timings injectable via ``clock``. Probe budget is Redis-atomic (SET NX).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from workers.redis_keys import (
    rubika_circuit_meta_key,
    rubika_circuit_probe_key,
    rubika_circuit_state_key,
    rubika_circuit_sys_accounts_key,
    rubika_circuit_sys_count_key,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("core_engine.services.rubika_circuit")


class RubikaCircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class RubikaCircuitSnapshot:
    state: str
    opened_at: str | None
    half_open_at: str | None
    open_until: str | None
    probe_budget: int
    probe_remaining: int
    systemic_account_count: int
    systemic_failure_count: int
    reason: str | None


_PROBE_ACQUIRE_SCRIPT = """
local probe_key = KEYS[1]
local state_key = KEYS[2]
local budget = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local token = ARGV[3]
local state = redis.call("GET", state_key)
if state ~= "half_open" then
  return {"deny", "0"}
end
local used = tonumber(redis.call("GET", probe_key) or "0")
if used >= budget then
  return {"deny", tostring(used)}
end
used = redis.call("INCR", probe_key)
if used == 1 then
  redis.call("EXPIRE", probe_key, ttl)
end
redis.call("SET", probe_key .. ":tok:" .. token, "1", "EX", ttl)
return {"ok", tostring(used)}
"""


def _now(clock: datetime | None = None) -> datetime:
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _window_bucket(now: datetime, window_seconds: int) -> str:
    epoch = int(now.timestamp())
    return str(epoch // max(1, window_seconds))


async def get_circuit_state(redis: "Redis") -> RubikaCircuitState:
    raw = await redis.get(rubika_circuit_state_key())
    if not raw:
        return RubikaCircuitState.CLOSED
    value = _decode(raw).lower()
    try:
        return RubikaCircuitState(value)
    except ValueError:
        return RubikaCircuitState.CLOSED


async def get_circuit_snapshot(
    redis: "Redis",
    *,
    probe_budget: int = 1,
    window_seconds: int = 300,
    clock: datetime | None = None,
) -> RubikaCircuitSnapshot:
    state = await get_circuit_state(redis)
    meta_raw = await redis.get(rubika_circuit_meta_key())
    meta: dict[str, Any] = {}
    if meta_raw:
        try:
            meta = json.loads(_decode(meta_raw))
        except json.JSONDecodeError:
            meta = {}
    now = _now(clock)
    bucket = _window_bucket(now, window_seconds)
    acct_count = int(await redis.scard(rubika_circuit_sys_accounts_key(bucket)) or 0)
    fail_count = int(await redis.get(rubika_circuit_sys_count_key(bucket)) or 0)
    used = int(await redis.get(rubika_circuit_probe_key()) or 0)
    return RubikaCircuitSnapshot(
        state=state.value,
        opened_at=meta.get("opened_at"),
        half_open_at=meta.get("half_open_at"),
        open_until=meta.get("open_until"),
        probe_budget=probe_budget,
        probe_remaining=max(0, probe_budget - used) if state == RubikaCircuitState.HALF_OPEN else 0,
        systemic_account_count=acct_count,
        systemic_failure_count=fail_count,
        reason=meta.get("reason"),
    )


async def _set_state(
    redis: "Redis",
    state: RubikaCircuitState,
    *,
    reason: str | None,
    clock: datetime | None,
    open_seconds: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    now = _now(clock)
    meta_raw = await redis.get(rubika_circuit_meta_key())
    meta: dict[str, Any] = {}
    if meta_raw:
        try:
            meta = json.loads(_decode(meta_raw))
        except json.JSONDecodeError:
            meta = {}
    meta.update(extra or {})
    meta["reason"] = reason
    meta["updated_at"] = _iso(now)
    if state == RubikaCircuitState.OPEN:
        meta["opened_at"] = _iso(now)
        if open_seconds:
            until = now.timestamp() + open_seconds
            meta["open_until"] = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
        await redis.delete(rubika_circuit_probe_key())
        logger.warning("event=rubika_circuit_opened reason=%s", reason)
    elif state == RubikaCircuitState.HALF_OPEN:
        meta["half_open_at"] = _iso(now)
        await redis.delete(rubika_circuit_probe_key())
        logger.warning("event=rubika_circuit_half_open reason=%s", reason)
    elif state == RubikaCircuitState.CLOSED:
        meta["closed_at"] = _iso(now)
        await redis.delete(rubika_circuit_probe_key())
        logger.info("event=rubika_circuit_closed reason=%s", reason)
    await redis.set(rubika_circuit_state_key(), state.value)
    await redis.set(rubika_circuit_meta_key(), json.dumps(meta))


async def open_circuit(
    redis: "Redis",
    *,
    reason: str,
    open_seconds: int = 120,
    clock: datetime | None = None,
) -> None:
    await _set_state(
        redis,
        RubikaCircuitState.OPEN,
        reason=reason,
        clock=clock,
        open_seconds=open_seconds,
    )


async def close_circuit(
    redis: "Redis",
    *,
    reason: str = "recovered",
    clock: datetime | None = None,
) -> None:
    await _set_state(redis, RubikaCircuitState.CLOSED, reason=reason, clock=clock)


async def enter_half_open(
    redis: "Redis",
    *,
    reason: str = "cooldown_elapsed",
    clock: datetime | None = None,
) -> None:
    await _set_state(redis, RubikaCircuitState.HALF_OPEN, reason=reason, clock=clock)


async def maybe_transition_open_to_half_open(
    redis: "Redis",
    *,
    clock: datetime | None = None,
) -> RubikaCircuitState:
    """If OPEN and open_until elapsed → HALF_OPEN."""
    state = await get_circuit_state(redis)
    if state != RubikaCircuitState.OPEN:
        return state
    meta_raw = await redis.get(rubika_circuit_meta_key())
    if not meta_raw:
        return state
    meta = json.loads(_decode(meta_raw))
    until = meta.get("open_until")
    if not until:
        return state
    try:
        until_dt = datetime.fromisoformat(until)
        if until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return state
    if _now(clock) >= until_dt.astimezone(timezone.utc):
        await enter_half_open(redis, clock=clock)
        return RubikaCircuitState.HALF_OPEN
    return state


async def record_systemic_failure(
    redis: "Redis",
    *,
    account_id: int | None,
    distinct_accounts_threshold: int = 3,
    failure_count_threshold: int = 15,
    window_seconds: int = 300,
    open_seconds: int = 120,
    clock: datetime | None = None,
    reason: str = "systemic_failures",
) -> bool:
    """Record a systemic-eligible failure. Returns True if circuit opened.

    Requires either:
    - distinct account cardinality ≥ threshold (when account_id provided), OR
    - global failure count ≥ threshold (connector-wide / dependency).
    A single account alone cannot open the breaker via cardinality path.
    """
    now = _now(clock)
    bucket = _window_bucket(now, window_seconds)
    acct_key = rubika_circuit_sys_accounts_key(bucket)
    count_key = rubika_circuit_sys_count_key(bucket)
    fail_count = int(await redis.incr(count_key))
    if fail_count == 1:
        await redis.expire(count_key, window_seconds * 2)

    distinct = 0
    if account_id is not None:
        await redis.sadd(acct_key, str(int(account_id)))
        await redis.expire(acct_key, window_seconds * 2)
        distinct = int(await redis.scard(acct_key) or 0)

    should_open = False
    if account_id is not None and distinct >= max(2, int(distinct_accounts_threshold)):
        should_open = True
        reason = f"{reason}:distinct_accounts={distinct}"
    elif fail_count >= int(failure_count_threshold) and (
        account_id is None or distinct >= 2
    ):
        # High volume alone still requires ≥2 accounts if account-scoped,
        # but dependency failures (account_id None) can open on count.
        should_open = True
        reason = f"{reason}:failure_count={fail_count}"

    if should_open:
        current = await get_circuit_state(redis)
        if current != RubikaCircuitState.OPEN:
            await open_circuit(
                redis, reason=reason, open_seconds=open_seconds, clock=clock
            )
            return True
    return False


async def try_acquire_probe(
    redis: "Redis",
    *,
    probe_budget: int = 1,
    probe_ttl_seconds: int = 60,
    clock: datetime | None = None,
) -> tuple[bool, str | None]:
    """Atomic HALF_OPEN probe permission. Returns (ok, token)."""
    await maybe_transition_open_to_half_open(redis, clock=clock)
    state = await get_circuit_state(redis)
    if state == RubikaCircuitState.CLOSED:
        return True, None  # no probe needed
    if state == RubikaCircuitState.OPEN:
        return False, None
    token = uuid.uuid4().hex
    result = await redis.eval(
        _PROBE_ACQUIRE_SCRIPT,
        2,
        rubika_circuit_probe_key(),
        rubika_circuit_state_key(),
        int(probe_budget),
        int(probe_ttl_seconds),
        token,
    )
    status = _decode(result[0]) if result else "deny"
    if status == "ok":
        return True, token
    return False, None


async def record_probe_success(
    redis: "Redis",
    *,
    successes_to_close: int = 1,
    clock: datetime | None = None,
) -> RubikaCircuitState:
    state = await get_circuit_state(redis)
    if state != RubikaCircuitState.HALF_OPEN:
        return state
    meta_raw = await redis.get(rubika_circuit_meta_key())
    meta: dict[str, Any] = {}
    if meta_raw:
        meta = json.loads(_decode(meta_raw))
    ok = int(meta.get("half_open_successes") or 0) + 1
    meta["half_open_successes"] = ok
    await redis.set(rubika_circuit_meta_key(), json.dumps(meta))
    if ok >= max(1, successes_to_close):
        await close_circuit(redis, reason="probe_success", clock=clock)
        return RubikaCircuitState.CLOSED
    return state


async def record_probe_failure(
    redis: "Redis",
    *,
    open_seconds: int = 120,
    reason: str = "probe_failed",
    clock: datetime | None = None,
) -> None:
    state = await get_circuit_state(redis)
    if state == RubikaCircuitState.HALF_OPEN:
        await open_circuit(
            redis, reason=reason, open_seconds=open_seconds, clock=clock
        )


async def assert_circuit_allows_send(
    redis: "Redis",
    *,
    probe_budget: int = 1,
    clock: datetime | None = None,
) -> tuple[bool, str | None, str | None]:
    """Preflight helper. Returns (allowed, deny_code_detail, probe_token)."""
    await maybe_transition_open_to_half_open(redis, clock=clock)
    state = await get_circuit_state(redis)
    if state == RubikaCircuitState.CLOSED:
        return True, None, None
    if state == RubikaCircuitState.OPEN:
        return False, "open", None
    ok, token = await try_acquire_probe(
        redis, probe_budget=probe_budget, clock=clock
    )
    if not ok:
        return False, "half_open_budget_exhausted", None
    return True, None, token
