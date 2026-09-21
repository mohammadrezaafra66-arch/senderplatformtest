"""Optional per-account hourly/daily message count limits.

NULL means unlimited for that dimension. A positive integer is the exact cap.
0 / negative / non-integers are invalid and must never be persisted or accepted
from the API. Redis Lua cannot encode SQL NULL, so reservation uses an internal
sentinel that is never stored in Postgres or accepted from JSON.
"""

from __future__ import annotations

from typing import Any

# Lua ARGV sentinel: cap > 0 enforces; cap == 0 skips deny and still INCR.
# Never persist this value in DB/API.
UNLIMITED_REDIS_SENTINEL = 0


class InvalidAccountMessageLimit(ValueError):
    """Raised when a count limit is not null and not a positive int."""

    def __init__(self, field: str, value: Any) -> None:
        self.field = field
        self.value = value
        super().__init__(f"{field} must be null or a positive integer")


def normalize_account_message_limit(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise InvalidAccountMessageLimit(field, value)
    return value


def is_unlimited_message_limit(limit: int | None) -> bool:
    return limit is None


def should_enforce_message_limit(limit: int | None) -> bool:
    return type(limit) is int and limit > 0


def remaining_for_limit(limit: int | None, used: int) -> int | None:
    if not should_enforce_message_limit(limit):
        return None
    return max(0, int(limit) - int(used))


def redis_cap_arg(limit: int | None) -> int:
    """Convert SQL/API NULL to the Lua sentinel. Reject negatives/non-ints."""
    if limit is None:
        return UNLIMITED_REDIS_SENTINEL
    if type(limit) is not int or limit < 0:
        raise InvalidAccountMessageLimit("redis_cap", limit)
    return limit


def resolve_account_count_limits(
    account: Any,
    *,
    hourly_override: int | None = None,
    daily_override: int | None = None,
) -> tuple[int | None, int | None]:
    """Return (hourly_limit, daily_limit). Overrides are for tests only.

    Env lifecycle caps are never used as fallbacks.
    """
    hourly = (
        hourly_override
        if hourly_override is not None
        else getattr(account, "hourly_message_limit", None)
    )
    daily = (
        daily_override
        if daily_override is not None
        else getattr(account, "daily_message_limit", None)
    )
    return hourly, daily


def account_hourly_limit(account: Any) -> int | None:
    value = getattr(account, "hourly_message_limit", None)
    return value if should_enforce_message_limit(value) else None


def account_daily_limit(account: Any) -> int | None:
    value = getattr(account, "daily_message_limit", None)
    return value if should_enforce_message_limit(value) else None
