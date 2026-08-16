"""Rubika Phase 3 — lifecycle + effective rate policy (application defaults).

Maps existing AccountStatus + warming_started_at + Redis cooldown/throttle
markers into an explicit lifecycle view WITHOUT a new DB enum.

These numeric defaults are **application policy**, not official Rubika
platform guarantees.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from core_engine.models import Account, AccountStatus

logger = logging.getLogger("core_engine.services.rubika_policy")

IRAN_TZ = ZoneInfo("Asia/Tehran")


class RubikaLifecycleState(str, Enum):
    NEW = "new"
    OBSERVATION = "observation"
    LIMITED = "limited"
    RAMPING = "ramping"
    NORMAL = "normal"
    THROTTLED = "throttled"
    COOLDOWN = "cooldown"
    SUSPENDED = "suspended"


# Application defaults (conservative). Overridable via WorkerSettings / kwargs.
_DEFAULT_STAGE_LIMITS: dict[RubikaLifecycleState, dict[str, int]] = {
    RubikaLifecycleState.NEW: {"daily": 3, "hourly": 1, "min_interval": 90},
    RubikaLifecycleState.OBSERVATION: {"daily": 5, "hourly": 2, "min_interval": 60},
    RubikaLifecycleState.LIMITED: {"daily": 15, "hourly": 5, "min_interval": 30},
    RubikaLifecycleState.RAMPING: {"daily": 50, "hourly": 15, "min_interval": 15},
    RubikaLifecycleState.NORMAL: {"daily": 100, "hourly": 50, "min_interval": 5},
}


@dataclass(frozen=True, slots=True)
class RubikaEffectiveLimits:
    daily_cap: int
    hourly_cap: int
    min_interval_seconds: int
    jitter_min_seconds: int
    jitter_max_seconds: int
    jitter_enabled: bool


@dataclass(frozen=True, slots=True)
class RubikaPolicySnapshot:
    account_id: int
    lifecycle_state: str
    warmup_day: int
    sent_today: int
    daily_cap: int
    remaining_daily: int
    sent_this_hour: int
    hourly_cap: int
    remaining_hourly: int
    minimum_interval_seconds: int
    last_send_at: str | None
    next_allowed_send_at: str | None
    cooldown_until: str | None
    cooldown_reason: str | None
    send_window_phase: str | None
    timezone: str
    allowed: bool
    code: str
    details: dict[str, Any] = field(default_factory=dict)


def policy_now(*, clock: datetime | None = None) -> datetime:
    """Current time in Asia/Tehran (aware). Inject ``clock`` for tests."""
    if clock is not None:
        if clock.tzinfo is None:
            return clock.replace(tzinfo=IRAN_TZ)
        return clock.astimezone(IRAN_TZ)
    return datetime.now(IRAN_TZ)


def rubika_day_bucket(now: datetime | None = None) -> str:
    return policy_now(clock=now).strftime("%Y%m%d")


def rubika_hour_bucket(now: datetime | None = None) -> str:
    return policy_now(clock=now).strftime("%Y%m%d%H")


def compute_warmup_day(
    warming_started_at: datetime | None,
    *,
    clock: datetime | None = None,
) -> int:
    """Days since warming anchor (Iran calendar day). NULL → day 0 (safest)."""
    now = policy_now(clock=clock)
    if warming_started_at is None:
        return 0
    started = warming_started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    started_local = started.astimezone(IRAN_TZ)
    return max(0, (now.date() - started_local.date()).days)


def lifecycle_from_warmup_day(day: int) -> RubikaLifecycleState:
    if day <= 0:
        return RubikaLifecycleState.NEW
    if day <= 2:
        return RubikaLifecycleState.OBSERVATION
    if day <= 6:
        return RubikaLifecycleState.LIMITED
    if day <= 13:
        return RubikaLifecycleState.RAMPING
    return RubikaLifecycleState.NORMAL


def resolve_rubika_lifecycle(
    account: Account,
    *,
    cooldown_active: bool = False,
    throttle_active: bool = False,
    clock: datetime | None = None,
) -> RubikaLifecycleState:
    """Map AccountStatus + warming + Redis overlays → lifecycle.

    Restart-safe: warming_started_at and Account.status are persisted.
    Redis overlays (cooldown/throttle) are ephemeral and re-evaluated.
    """
    if account.status in (AccountStatus.BANNED, AccountStatus.REQUIRES_LOGIN):
        return RubikaLifecycleState.SUSPENDED
    if cooldown_active:
        return RubikaLifecycleState.COOLDOWN
    if throttle_active or account.status == AccountStatus.RESTING:
        return RubikaLifecycleState.THROTTLED
    if account.status != AccountStatus.ACTIVE:
        return RubikaLifecycleState.SUSPENDED

    day = compute_warmup_day(getattr(account, "warming_started_at", None), clock=clock)
    if getattr(account, "warming_started_at", None) is None and getattr(account, "last_used_at", None) is None:
        return RubikaLifecycleState.NEW
    return lifecycle_from_warmup_day(day)


def resolve_effective_limits(
    lifecycle: RubikaLifecycleState,
    *,
    configured_daily_cap: int,
    configured_hourly_cap: int,
    configured_min_interval: int,
    configured_max_interval: int,
    jitter_enabled: bool = True,
    policy_daily_cap: int | None = None,
    policy_hourly_cap: int | None = None,
) -> RubikaEffectiveLimits:
    """Effective caps = min(stage default, configured/policy overrides)."""
    if lifecycle in (
        RubikaLifecycleState.SUSPENDED,
        RubikaLifecycleState.COOLDOWN,
        RubikaLifecycleState.THROTTLED,
    ):
        # Non-sendable — caps unused but keep deterministic values.
        stage = _DEFAULT_STAGE_LIMITS[RubikaLifecycleState.NEW]
    else:
        stage = _DEFAULT_STAGE_LIMITS.get(
            lifecycle, _DEFAULT_STAGE_LIMITS[RubikaLifecycleState.NORMAL]
        )

    daily = int(stage["daily"])
    hourly = int(stage["hourly"])
    min_interval = int(stage["min_interval"])

    # Configured env caps act as ceilings for NORMAL; for earlier stages use min().
    cfg_daily = int(policy_daily_cap if policy_daily_cap is not None else configured_daily_cap)
    cfg_hourly = int(policy_hourly_cap if policy_hourly_cap is not None else configured_hourly_cap)
    cfg_min = max(1, int(configured_min_interval))
    cfg_max = max(cfg_min, int(configured_max_interval))

    if lifecycle == RubikaLifecycleState.NORMAL:
        daily = cfg_daily
        hourly = cfg_hourly
        min_interval = cfg_min
    else:
        daily = min(daily, cfg_daily)
        hourly = min(hourly, cfg_hourly)
        min_interval = max(min_interval, cfg_min)

    return RubikaEffectiveLimits(
        daily_cap=max(1, daily),
        hourly_cap=max(1, hourly),
        min_interval_seconds=max(1, min_interval),
        jitter_min_seconds=min_interval,
        jitter_max_seconds=cfg_max if jitter_enabled else min_interval,
        jitter_enabled=bool(jitter_enabled),
    )


def compute_jitter_seconds(
    limits: RubikaEffectiveLimits,
    *,
    rng: Any | None = None,
) -> int:
    """Bounded jitter; never below min_interval safety floor."""
    low = int(limits.min_interval_seconds)
    high = int(limits.jitter_max_seconds)
    if not limits.jitter_enabled or high <= low:
        return low
    if rng is None:
        import random

        rng = random
    value = int(rng.randint(low, high))
    return max(low, value)


def ensure_warming_started(account: Account, *, clock: datetime | None = None) -> bool:
    """Persist warming anchor on first real use. Returns True if updated."""
    if getattr(account, "warming_started_at", None) is not None:
        return False
    account.warming_started_at = policy_now(clock=clock).astimezone(timezone.utc)
    return True


def build_policy_snapshot(
    *,
    account_id: int,
    lifecycle: RubikaLifecycleState,
    warmup_day: int,
    limits: RubikaEffectiveLimits,
    sent_today: int,
    sent_this_hour: int,
    allowed: bool,
    code: str,
    next_allowed_send_at: str | None = None,
    cooldown_until: str | None = None,
    cooldown_reason: str | None = None,
    last_send_at: str | None = None,
    send_window_phase: str | None = None,
    details: dict[str, Any] | None = None,
) -> RubikaPolicySnapshot:
    return RubikaPolicySnapshot(
        account_id=account_id,
        lifecycle_state=lifecycle.value,
        warmup_day=warmup_day,
        sent_today=sent_today,
        daily_cap=limits.daily_cap,
        remaining_daily=max(0, limits.daily_cap - sent_today),
        sent_this_hour=sent_this_hour,
        hourly_cap=limits.hourly_cap,
        remaining_hourly=max(0, limits.hourly_cap - sent_this_hour),
        minimum_interval_seconds=limits.min_interval_seconds,
        last_send_at=last_send_at,
        next_allowed_send_at=next_allowed_send_at,
        cooldown_until=cooldown_until,
        cooldown_reason=cooldown_reason,
        send_window_phase=send_window_phase,
        timezone="Asia/Tehran",
        allowed=allowed,
        code=code,
        details=details or {},
    )
