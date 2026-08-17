"""Map Rubika preflight/quota denials to retry-at hints (Phase 6).

Does not authorize sends. Never uses a fixed 1-second retry for policy waits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from core_engine.services.campaign_capacity import (
    SendWindowSpec,
    next_hour_boundary,
    next_policy_day_start,
    next_window_start,
)
from core_engine.services.rubika_policy import policy_now

# Inline worker sleep ceiling — longer waits go to the delayed Redis ZSET.
MAX_INLINE_RETRY_SLEEP_SECONDS = 15
MIN_POLICY_RETRY_SECONDS = 5


@dataclass(frozen=True, slots=True)
class RetryHint:
    code: str
    retry_at: datetime | None
    delay_seconds: int
    use_delayed_queue: bool
    reason: str


def _parse_iso(value: object | None) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return policy_now(clock=parsed)


def _delay(now: datetime, target: datetime | None, *, fallback: int) -> int:
    if target is None:
        return max(MIN_POLICY_RETRY_SECONDS, fallback)
    seconds = int((target - now).total_seconds())
    return max(MIN_POLICY_RETRY_SECONDS, seconds)


def retry_hint_for_code(
    code: str,
    *,
    now: datetime,
    details: dict[str, Any] | None = None,
    windows: Sequence[SendWindowSpec] = (),
) -> RetryHint:
    local = policy_now(clock=now)
    payload = details or {}
    normalized = str(code or "").upper()
    if normalized.startswith("RUBIKA_"):
        inner = normalized[len("RUBIKA_") :]
    else:
        inner = normalized

    retry_at: datetime | None
    fallback = 60
    reason = inner

    if inner in {"COOLDOWN_ACTIVE", "ACCOUNT_THROTTLED"}:
        retry_at = _parse_iso(payload.get("cooldown_until") or payload.get("until"))
        fallback = 300
        reason = "cooldown_until"
    elif inner == "MIN_INTERVAL_ACTIVE":
        retry_after = payload.get("retry_after_seconds")
        next_at = _parse_iso(payload.get("next_allowed_at"))
        if next_at is not None:
            retry_at = next_at
        elif retry_after is not None:
            try:
                retry_at = local + timedelta(seconds=max(1, int(retry_after)))
            except (TypeError, ValueError):
                retry_at = local + timedelta(seconds=MIN_POLICY_RETRY_SECONDS)
        else:
            retry_at = local + timedelta(seconds=MIN_POLICY_RETRY_SECONDS)
        reason = "next_allowed_at"
        fallback = MIN_POLICY_RETRY_SECONDS
    elif inner == "HOURLY_CAP_REACHED":
        retry_at = next_hour_boundary(local)
        reason = "next_hour_bucket"
        fallback = 3600
    elif inner == "DAILY_CAP_REACHED":
        retry_at = next_policy_day_start(local)
        reason = "next_policy_day"
        fallback = 3600
    elif inner == "OUTSIDE_SEND_WINDOW":
        retry_at = next_window_start(windows, local)
        reason = "next_window_start"
        fallback = 900
    elif inner in {"RUBIKA_CIRCUIT_OPEN", "CIRCUIT_OPEN"}:
        retry_at = _parse_iso(payload.get("open_until"))
        reason = "circuit_open_until"
        fallback = 120
    elif inner == "REDIS_UNAVAILABLE":
        retry_at = local + timedelta(seconds=30)
        reason = "redis_retry"
        fallback = 30
    else:
        retry_at = local + timedelta(seconds=max(MIN_POLICY_RETRY_SECONDS, 20))
        reason = "generic_backoff"
        fallback = 20

    delay = _delay(local, retry_at, fallback=fallback)
    return RetryHint(
        code=normalized or "UNKNOWN",
        retry_at=retry_at,
        delay_seconds=delay,
        use_delayed_queue=delay > MAX_INLINE_RETRY_SLEEP_SECONDS,
        reason=reason,
    )


def compute_policy_retry_delay_seconds(
    attempt: int,
    base_delay_seconds: float,
    *,
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
    windows: Sequence[SendWindowSpec] = (),
) -> tuple[float, bool]:
    """Return (delay_seconds, use_delayed_queue).

    Policy codes use expiry-based hints. Other retryable errors keep exponential
    backoff with a floor of ``base_delay_seconds``.
    """
    if error_code:
        hint = retry_hint_for_code(
            error_code,
            now=now or policy_now(),
            details=details,
            windows=windows,
        )
        inner = hint.code.upper()
        if inner.startswith("RUBIKA_"):
            inner = inner[len("RUBIKA_") :]
        if inner in {
            "COOLDOWN_ACTIVE",
            "MIN_INTERVAL_ACTIVE",
            "HOURLY_CAP_REACHED",
            "DAILY_CAP_REACHED",
            "OUTSIDE_SEND_WINDOW",
            "CIRCUIT_OPEN",
            "ACCOUNT_THROTTLED",
            "REDIS_UNAVAILABLE",
        } or hint.code.upper() in {"RUBIKA_CIRCUIT_OPEN"}:
            return float(hint.delay_seconds), hint.use_delayed_queue

    exponent = max(int(attempt), 1) - 1
    delay = max(0.0, float(base_delay_seconds)) * (2**exponent)
    return delay, delay > MAX_INLINE_RETRY_SLEEP_SECONDS
