"""Pure Rubika campaign capacity planner (Phase 6).

No DB, Redis, or network. Callers inject ``now`` (Asia/Tehran) and evidence
gathered from application policy snapshots — never invented platform limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence

from core_engine.services.rubika_policy import IRAN_TZ, policy_now

TIMEZONE_NAME = "Asia/Tehran"

# Hard blocks: no safe send path for this account until operator/system changes.
HARD_BLOCK_CODES = frozenset(
    {
        "ACCOUNT_BANNED",
        "ACCOUNT_REQUIRES_LOGIN",
        "ACCOUNT_SUSPENDED",
        "ACCOUNT_QUARANTINED",
        "ACCOUNT_DISABLED",
        "SESSION_MISSING",
        "SESSION_INVALID",
        "SESSION_DECRYPT_FAILED",
        "ACCOUNT_IDENTIFIER_MISSING",
        "CAMPAIGN_ACCOUNT_NOT_ALLOWED",
        "USER_ACCOUNT_DISABLED",
        "DELIVERY_MODE_DISABLED",
        "CONFIG_INVALID",
        "WRONG_PLATFORM",
        "ACCOUNT_MISSING",
    }
)

TEMPORARY_CODES = frozenset(
    {
        "COOLDOWN_ACTIVE",
        "MIN_INTERVAL_ACTIVE",
        "HOURLY_CAP_REACHED",
        "DAILY_CAP_REACHED",
        "OUTSIDE_SEND_WINDOW",
        "ACCOUNT_THROTTLED",
        "RUBIKA_CIRCUIT_OPEN",
        "ACCOUNT_NOT_IN_ALLOWED_POOL",
        "REDIS_UNAVAILABLE",
    }
)


@dataclass(frozen=True, slots=True)
class SendWindowSpec:
    phase: str
    start_hour: int
    end_hour: int


@dataclass(frozen=True, slots=True)
class AccountCapacityInput:
    account_id: int
    assigned_remaining: int
    label: str | None = None
    health: str | None = None
    readiness: str | None = None
    lifecycle: str | None = None
    remaining_daily: int | None = None
    remaining_hourly: int | None = None
    daily_cap: int | None = None
    hourly_cap: int | None = None
    min_interval_seconds: int = 0
    next_allowed_at: datetime | None = None
    cooldown_until: datetime | None = None
    window_open: bool = True
    applies_quota: bool = True
    block_code: str | None = None
    delivery_mode: str | None = None


@dataclass(frozen=True, slots=True)
class AccountCapacityPlan:
    account_id: int
    label: str | None
    assigned_remaining: int
    health: str | None
    readiness: str | None
    lifecycle: str | None
    remaining_daily: int | None
    remaining_hourly: int | None
    daily_cap: int | None
    hourly_cap: int | None
    next_allowed_at: datetime | None
    cooldown_until: datetime | None
    window_open: bool
    eligible_now: bool
    is_temporary: bool
    is_hard_blocked: bool
    is_bottleneck: bool
    block_code: str | None
    immediate_capacity: int
    today_capacity: int | None
    bottleneck_reason: str | None
    delivery_mode: str | None


@dataclass(frozen=True, slots=True)
class CompletionEstimate:
    estimated_completion_at: datetime | None
    estimated_duration_seconds: int | None
    policy_days: int | None
    confidence: str
    limitations: tuple[str, ...]
    is_estimate: bool = True


@dataclass(frozen=True, slots=True)
class CampaignCapacityAggregate:
    assigned_accounts: int
    usable_now: int
    temporary: int
    blocked: int
    total_assigned_remaining: int
    immediate_capacity: int
    estimated_today_capacity: int | None
    estimated_hourly_capacity: int | None
    today_confidence: str
    completion: CompletionEstimate
    bottleneck_account_ids: tuple[int, ...]
    accounts: tuple[AccountCapacityPlan, ...]
    timezone: str = TIMEZONE_NAME


def hour_in_window(hour: int, start: int, end: int) -> bool:
    hour = int(hour) % 24
    start = int(start)
    end = int(end)
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def current_window_phase(
    windows: Sequence[SendWindowSpec],
    now: datetime,
) -> str | None:
    local = policy_now(clock=now)
    hour = local.hour
    for window in windows:
        if hour_in_window(hour, window.start_hour, window.end_hour):
            return window.phase
    return None


def next_window_start(
    windows: Sequence[SendWindowSpec],
    now: datetime,
) -> datetime | None:
    """Next Iran-local datetime when any active window opens (inclusive)."""
    if not windows:
        return None
    local = policy_now(clock=now)
    if current_window_phase(windows, local) is not None:
        return local
    start = local.replace(minute=0, second=0, microsecond=0)
    for offset in range(0, 24 * 8):
        candidate = start + timedelta(hours=offset)
        if current_window_phase(windows, candidate) is not None:
            return candidate
    return None


def remaining_window_hours_today(
    windows: Sequence[SendWindowSpec],
    now: datetime,
) -> int:
    """Whole Iran hours from the current hour through 23 that sit in a window."""
    local = policy_now(clock=now)
    if not windows:
        return 24 - local.hour
    count = 0
    for hour in range(local.hour, 24):
        if any(hour_in_window(hour, w.start_hour, w.end_hour) for w in windows):
            count += 1
    return count


def window_hours_per_policy_day(windows: Sequence[SendWindowSpec]) -> int:
    if not windows:
        return 24
    count = 0
    for hour in range(24):
        if any(hour_in_window(hour, w.start_hour, w.end_hour) for w in windows):
            count += 1
    return count


def next_hour_boundary(now: datetime) -> datetime:
    local = policy_now(clock=now)
    return (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))


def next_policy_day_start(now: datetime) -> datetime:
    local = policy_now(clock=now)
    return (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _is_hard_blocked(code: str | None) -> bool:
    return bool(code) and code in HARD_BLOCK_CODES


def _is_temporary(code: str | None) -> bool:
    return bool(code) and code in TEMPORARY_CODES


def account_eligible_now(row: AccountCapacityInput) -> bool:
    if row.assigned_remaining <= 0:
        return False
    if _is_hard_blocked(row.block_code):
        return False
    if row.block_code in {"RUBIKA_CIRCUIT_OPEN", "REDIS_UNAVAILABLE"}:
        return False
    if not row.window_open and row.applies_quota:
        return False
    if row.applies_quota:
        if (row.remaining_daily or 0) <= 0:
            return False
        if (row.remaining_hourly or 0) <= 0:
            return False
    if row.block_code in {
        "COOLDOWN_ACTIVE",
        "MIN_INTERVAL_ACTIVE",
        "ACCOUNT_THROTTLED",
        "HOURLY_CAP_REACHED",
        "DAILY_CAP_REACHED",
        "OUTSIDE_SEND_WINDOW",
        "ACCOUNT_NOT_IN_ALLOWED_POOL",
    }:
        return False
    return True


def immediate_slots_for_account(row: AccountCapacityInput) -> int:
    if not account_eligible_now(row):
        return 0
    if not row.applies_quota:
        return 1
    slot = min(
        row.assigned_remaining,
        max(0, int(row.remaining_hourly or 0)),
        max(0, int(row.remaining_daily or 0)),
    )
    if row.min_interval_seconds > 0:
        slot = min(slot, 1)
    return max(0, slot)


def today_slots_for_account(
    row: AccountCapacityInput,
    *,
    windows: Sequence[SendWindowSpec],
    now: datetime,
) -> int | None:
    """Remaining eligible sends for this account before the Iran day boundary.

    None means not applicable / unknown (e.g. bot_api without daily policy).
    """
    if row.assigned_remaining <= 0:
        return 0
    if _is_hard_blocked(row.block_code):
        return 0
    if not row.applies_quota:
        return None
    remaining_daily = max(0, int(row.remaining_daily or 0))
    remaining_hourly = max(0, int(row.remaining_hourly or 0))
    hourly_cap = max(0, int(row.hourly_cap or 0))
    hours_left = remaining_window_hours_today(windows, now)
    in_window = row.window_open
    this_hour = remaining_hourly if in_window else 0
    future_hours = max(0, hours_left - (1 if in_window else 0))
    rest = future_hours * hourly_cap
    raw = this_hour + rest
    return min(row.assigned_remaining, remaining_daily, raw)


def effective_daily_throughput(
    row: AccountCapacityInput,
    *,
    windows: Sequence[SendWindowSpec],
) -> int | None:
    if not row.applies_quota:
        if row.min_interval_seconds > 0:
            hours = window_hours_per_policy_day(windows) if windows else 24
            per_hour = max(1, int(3600 / max(1, row.min_interval_seconds)))
            return hours * per_hour
        return None
    hours = window_hours_per_policy_day(windows)
    hourly_cap = max(0, int(row.hourly_cap or 0))
    daily_cap = max(0, int(row.daily_cap or 0))
    interval = max(0, int(row.min_interval_seconds or 0))
    interval_per_hour = int(3600 / interval) if interval > 0 else hourly_cap
    hourly_effective = min(hourly_cap, interval_per_hour) if hourly_cap else interval_per_hour
    return min(daily_cap, hourly_effective * hours) if daily_cap else hourly_effective * hours


def estimate_account_completion(
    row: AccountCapacityInput,
    *,
    windows: Sequence[SendWindowSpec],
    now: datetime,
) -> CompletionEstimate:
    local = policy_now(clock=now)
    remaining = max(0, int(row.assigned_remaining))
    if remaining == 0:
        return CompletionEstimate(
            estimated_completion_at=local,
            estimated_duration_seconds=0,
            policy_days=0,
            confidence="exact",
            limitations=(),
        )
    if _is_hard_blocked(row.block_code):
        return CompletionEstimate(
            estimated_completion_at=None,
            estimated_duration_seconds=None,
            policy_days=None,
            confidence="blocked",
            limitations=("hard_blocked_sender",),
        )

    first_today = today_slots_for_account(row, windows=windows, now=local)
    daily = effective_daily_throughput(row, windows=windows)
    limitations: list[str] = ["estimate_only", "no_silent_reassignment"]
    if not row.applies_quota:
        limitations.append("bot_api_quota_not_applicable")

    if daily is None or daily <= 0:
        return CompletionEstimate(
            estimated_completion_at=None,
            estimated_duration_seconds=None,
            policy_days=None,
            confidence="unknown",
            limitations=tuple(limitations + ["throughput_unknown"]),
        )

    leftover = remaining
    days = 0
    if first_today is not None:
        leftover = max(0, leftover - first_today)
        days = 1 if remaining > 0 else 0
    if leftover > 0:
        extra = math.ceil(leftover / daily)
        days += extra
    days = max(days, 1 if remaining else 0)
    # Anchor to next window if currently closed.
    start = local
    if row.applies_quota and not row.window_open:
        nxt = next_window_start(windows, local)
        if nxt is not None:
            start = nxt
    completion_at = next_policy_day_start(start - timedelta(seconds=1)) + timedelta(
        days=max(0, days - 1)
    )
    # Stay inside a window when possible.
    if windows:
        aligned = next_window_start(windows, completion_at)
        if aligned is not None:
            completion_at = aligned
    duration = max(0, int((completion_at - local).total_seconds()))
    return CompletionEstimate(
        estimated_completion_at=completion_at,
        estimated_duration_seconds=duration,
        policy_days=days,
        confidence="bounded",
        limitations=tuple(limitations),
    )


def _bottleneck_reason(row: AccountCapacityInput, today: int | None) -> str | None:
    if row.assigned_remaining <= 0:
        return None
    if _is_hard_blocked(row.block_code):
        return row.block_code or "hard_blocked"
    if today is not None and row.assigned_remaining > today:
        return "assigned_exceeds_today_capacity"
    if row.applies_quota and (row.remaining_daily or 0) < row.assigned_remaining:
        return "daily_remaining_below_assignment"
    return None


def aggregate_campaign_capacity(
    rows: Sequence[AccountCapacityInput],
    *,
    now: datetime,
    windows: Sequence[SendWindowSpec] = (),
) -> CampaignCapacityAggregate:
    local = policy_now(clock=now)
    plans: list[AccountCapacityPlan] = []
    usable = temporary = blocked = 0
    immediate = 0
    today_known = True
    today_total = 0
    hourly_known = False
    hourly_total = 0
    completions: list[CompletionEstimate] = []
    bottleneck_ids: list[int] = []

    for row in rows:
        eligible = account_eligible_now(row)
        hard = _is_hard_blocked(row.block_code)
        temp = (not hard) and (
            _is_temporary(row.block_code) or (row.applies_quota and not row.window_open)
        )
        if eligible:
            usable += 1
        elif hard:
            blocked += 1
        elif temp or row.assigned_remaining > 0:
            temporary += 1
        imm = immediate_slots_for_account(row)
        immediate += imm
        today = today_slots_for_account(row, windows=windows, now=local)
        if today is None:
            today_known = False
        else:
            today_total += today
        if row.applies_quota and row.remaining_hourly is not None and not hard:
            hourly_known = True
            hourly_total += min(max(0, row.assigned_remaining), max(0, row.remaining_hourly))
        reason = _bottleneck_reason(row, today)
        is_bottleneck = bool(reason) and row.assigned_remaining > 0
        if is_bottleneck:
            bottleneck_ids.append(row.account_id)
        plans.append(
            AccountCapacityPlan(
                account_id=row.account_id,
                label=row.label,
                assigned_remaining=row.assigned_remaining,
                health=row.health,
                readiness=row.readiness,
                lifecycle=row.lifecycle,
                remaining_daily=row.remaining_daily,
                remaining_hourly=row.remaining_hourly,
                daily_cap=row.daily_cap,
                hourly_cap=row.hourly_cap,
                next_allowed_at=row.next_allowed_at,
                cooldown_until=row.cooldown_until,
                window_open=row.window_open,
                eligible_now=eligible,
                is_temporary=temp and not eligible,
                is_hard_blocked=hard,
                is_bottleneck=is_bottleneck,
                block_code=row.block_code,
                immediate_capacity=imm,
                today_capacity=today,
                bottleneck_reason=reason,
                delivery_mode=row.delivery_mode,
            )
        )
        completions.append(estimate_account_completion(row, windows=windows, now=local))

    remaining_total = sum(max(0, r.assigned_remaining) for r in rows)
    limitations: list[str] = ["estimate_only", "no_silent_reassignment"]
    completion_at: datetime | None = None
    duration: int | None = 0
    policy_days = 0
    confidence = "bounded"
    if remaining_total == 0:
        completion_at = local
        duration = 0
        policy_days = 0
        confidence = "exact"
    else:
        blocked_remaining = any(
            r.assigned_remaining > 0 and _is_hard_blocked(r.block_code) for r in rows
        )
        if blocked_remaining:
            limitations.append("hard_blocked_sender_has_assigned_messages")
            confidence = "blocked"
            completion_at = None
            duration = None
            policy_days = None
        else:
            for item in completions:
                limitations.extend(item.limitations)
                if item.estimated_completion_at is None:
                    confidence = "unknown"
                    completion_at = None
                    duration = None
                    policy_days = None
                    break
                if completion_at is None or item.estimated_completion_at > completion_at:
                    completion_at = item.estimated_completion_at
                if item.policy_days is not None:
                    policy_days = max(policy_days, item.policy_days)
            if completion_at is not None:
                duration = max(0, int((completion_at - local).total_seconds()))

    unique_limits = tuple(dict.fromkeys(limitations))
    return CampaignCapacityAggregate(
        assigned_accounts=len(rows),
        usable_now=usable,
        temporary=temporary,
        blocked=blocked,
        total_assigned_remaining=remaining_total,
        immediate_capacity=immediate,
        estimated_today_capacity=None if not today_known else today_total,
        estimated_hourly_capacity=hourly_total if hourly_known else None,
        today_confidence="bounded" if today_known else "not_applicable",
        completion=CompletionEstimate(
            estimated_completion_at=completion_at,
            estimated_duration_seconds=duration,
            policy_days=policy_days,
            confidence=confidence,
            limitations=unique_limits,
        ),
        bottleneck_account_ids=tuple(bottleneck_ids),
        accounts=tuple(plans),
    )


def plan_to_matrix_row(plan: AccountCapacityPlan) -> dict[str, Any]:
    return {
        "account": plan.account_id,
        "assigned": plan.assigned_remaining,
        "health": plan.health,
        "readiness": plan.readiness,
        "daily_remaining": plan.remaining_daily,
        "hourly_remaining": plan.remaining_hourly,
        "window": "open" if plan.window_open else "closed",
        "cooldown": plan.cooldown_until.isoformat() if plan.cooldown_until else None,
        "eligible_now": plan.eligible_now,
        "bottleneck": plan.is_bottleneck,
        "reason": plan.bottleneck_reason or plan.block_code,
    }
