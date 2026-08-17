"""Central campaign-level send preflight (Phase 6).

Planning only: never consumes quota, never reassigns senders, never mutates
frozen final_text. Account-level preflight/quota/circuit remain authoritative
at the transport boundary.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Message,
    PlatformType,
    RenderedMessage,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.account_session_wiring import evaluate_account_session_readiness
from core_engine.services.campaign_capacity import (
    AccountCapacityInput,
    CampaignCapacityAggregate,
    SendWindowSpec,
    aggregate_campaign_capacity,
    current_window_phase,
    next_window_start,
)
from core_engine.services.campaign_inflight import get_campaign_safety_pause
from core_engine.services.rubika_mode import (
    RUBIKA_MODE_USER_ACCOUNT,
    resolve_rubika_delivery_mode,
)
from core_engine.services.rubika_policy import (
    IRAN_TZ,
    policy_now,
    resolve_effective_limits,
    resolve_rubika_lifecycle,
)

logger = logging.getLogger("core_engine.services.campaign_preflight")

CAMPAIGN_READY = "CAMPAIGN_READY"
CAMPAIGN_NOT_PREPARED = "CAMPAIGN_NOT_PREPARED"
CAMPAIGN_NO_MESSAGES = "CAMPAIGN_NO_MESSAGES"
CAMPAIGN_NO_SENDERS = "CAMPAIGN_NO_SENDERS"
CAMPAIGN_SENDER_BLOCKED = "CAMPAIGN_SENDER_BLOCKED"
CAMPAIGN_INSUFFICIENT_CAPACITY = "CAMPAIGN_INSUFFICIENT_CAPACITY"
CAMPAIGN_OUTSIDE_SEND_WINDOW = "CAMPAIGN_OUTSIDE_SEND_WINDOW"
CAMPAIGN_CIRCUIT_OPEN = "CAMPAIGN_CIRCUIT_OPEN"
CAMPAIGN_ALL_ACCOUNTS_QUARANTINED = "CAMPAIGN_ALL_ACCOUNTS_QUARANTINED"
CAMPAIGN_CONFIGURATION_INVALID = "CAMPAIGN_CONFIGURATION_INVALID"
CAMPAIGN_CAPACITY_UNKNOWN = "CAMPAIGN_CAPACITY_UNKNOWN"
CAMPAIGN_ALREADY_RUNNING = "CAMPAIGN_ALREADY_RUNNING"
CAMPAIGN_DEPENDENCY_ERROR = "CAMPAIGN_DEPENDENCY_ERROR"

EXEC_READY = "READY"
EXEC_CAPACITY_WARNING = "CAPACITY_WARNING"
EXEC_BLOCKED = "BLOCKED"
EXEC_RUNNING = "RUNNING"
EXEC_PAUSED_SAFETY = "PAUSED_SAFETY"
EXEC_PAUSED_OPERATOR = "PAUSED_OPERATOR"
EXEC_WAITING_WINDOW = "WAITING_WINDOW"
EXEC_WAITING_CAPACITY = "WAITING_CAPACITY"
EXEC_COMPLETED = "COMPLETED"
EXEC_FAILED = "FAILED"
EXEC_READY_TO_RESUME = "READY_TO_RESUME"

_TERMINAL_SEND = (
    SendStatus.DELIVERED,
    SendStatus.READ,
    SendStatus.FAILED_PERMANENT,
    SendStatus.OPTED_OUT,
    SendStatus.BLACKLISTED,
    SendStatus.DRY_RUN,
    SendStatus.SHADOW_SENT,
)

_PENDING_SEND = (
    SendStatus.PENDING,
    SendStatus.QUEUED,
    SendStatus.PROCESSING,
    SendStatus.ACCEPTED_BY_WORKER,
    SendStatus.ACCEPTED_BY_PLATFORM,
    SendStatus.FAILED_RETRYABLE,
)

_PERSIAN = {
    CAMPAIGN_READY: "کمپین از نظر ایمنی ارسال آماده است.",
    CAMPAIGN_NOT_PREPARED: "کمپین هنوز آماده‌سازی نشده است.",
    CAMPAIGN_NO_MESSAGES: "هیچ پیام آماده‌ای برای ارسال وجود ندارد.",
    CAMPAIGN_NO_SENDERS: "هیچ فرستنده تخصیص‌یافته‌ای برای این کمپین وجود ندارد.",
    CAMPAIGN_SENDER_BLOCKED: "همه اکانت‌های تخصیص‌یافته در حال حاضر غیرقابل ارسال هستند.",
    CAMPAIGN_INSUFFICIENT_CAPACITY: "ظرفیت ایمن فعلی برای شروع ارسال کافی نیست.",
    CAMPAIGN_OUTSIDE_SEND_WINDOW: "الان خارج از بازه زمانی ارسال است؛ کمپین در انتظار پنجره می‌ماند.",
    CAMPAIGN_CIRCUIT_OPEN: "ارسال روبیکا به دلیل فعال بودن حفاظت سراسری متوقف است.",
    CAMPAIGN_ALL_ACCOUNTS_QUARANTINED: "همه اکانت‌های تخصیص‌یافته در قرنطینه هستند.",
    CAMPAIGN_CONFIGURATION_INVALID: "پیکربندی کمپین برای ارسال معتبر نیست.",
    CAMPAIGN_CAPACITY_UNKNOWN: "وضعیت ظرفیت به دلیل اختلال Redis قابل بررسی نیست.",
    CAMPAIGN_ALREADY_RUNNING: "کمپین هم‌اکنون در حال اجرا است.",
    CAMPAIGN_DEPENDENCY_ERROR: "خطای وابستگی سیستمی؛ وضعیت کمپین تغییر نکرد.",
}

MULTI_DAY_WARNING = "این کمپین با ظرفیت فعلی در چند بازه/روز تکمیل خواهد شد."


@dataclass
class CampaignProgressCounts:
    total: int = 0
    prepared: int = 0
    pending: int = 0
    ready: int = 0
    queued: int = 0
    in_flight: int = 0
    delivered: int = 0
    failed_retryable: int = 0
    failed_permanent: int = 0
    waiting_capacity: int = 0
    waiting_window: int = 0
    blocked_sender: int = 0


@dataclass
class CampaignPreflightResult:
    allowed_to_start: bool
    code: str
    message: str
    campaign_id: int
    execution_safety_state: str
    total_messages: int
    ready_messages: int
    blocked_messages: int
    assigned_accounts: int
    usable_accounts: int
    blocked_accounts: int
    temporary_accounts: int
    immediate_capacity: int | None
    estimated_today_capacity: int | None
    estimated_completion_at: str | None
    estimated_duration_seconds: int | None
    timezone: str
    estimated_hourly_capacity: int | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    progress: dict[str, int] = field(default_factory=dict)
    sender_selection_mode: str = "automatic"
    delivery_mode: str | None = None
    circuit_state: str | None = None
    next_window_start: str | None = None
    resume_policy: str = "operator"
    ready_to_resume: bool = False
    capacity_confidence: str | None = None
    limitations: list[str] = field(default_factory=list)
    evaluated_at: str = ""
    redis_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def log_campaign_safety_event(
    event: str,
    *,
    campaign_id: int,
    code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = extra or {}
    logger.info(
        "event=%s campaign_id=%s code=%s details=%s",
        event,
        campaign_id,
        code,
        {k: v for k, v in payload.items() if k not in {"final_text", "message_text"}},
    )


def load_send_windows(db: Session) -> list[SendWindowSpec]:
    from core_engine.models import RubikaSenderSchedule

    rows = (
        db.query(RubikaSenderSchedule)
        .filter(RubikaSenderSchedule.is_active.is_(True))
        .order_by(RubikaSenderSchedule.id.asc())
        .all()
    )
    return [
        SendWindowSpec(phase=row.phase, start_hour=int(row.start_hour), end_hour=int(row.end_hour))
        for row in rows
        if int(row.start_hour) != int(row.end_hour)
    ]


def _status_counts(db: Session, campaign_id: int) -> dict[str, int]:
    rows = (
        db.query(CampaignRecipient.send_status, func.count(CampaignRecipient.id))
        .filter(CampaignRecipient.campaign_id == campaign_id)
        .group_by(CampaignRecipient.send_status)
        .all()
    )
    out: dict[str, int] = {}
    for status, count in rows:
        key = status.value if hasattr(status, "value") else str(status)
        out[key] = int(count or 0)
    return out


def _staged_counts(db: Session, campaign_id: int) -> dict[str, int]:
    rows = (
        db.query(StagedQueueItem.status, func.count(StagedQueueItem.id))
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .group_by(StagedQueueItem.status)
        .all()
    )
    return {str(status): int(count or 0) for status, count in rows}


def _assignment_remaining(db: Session, campaign_id: int) -> dict[int, int]:
    rows = (
        db.query(Message.account_id, func.count(Message.id))
        .join(CampaignRecipient, CampaignRecipient.final_message_id == Message.id)
        .filter(
            Message.campaign_id == campaign_id,
            CampaignRecipient.send_status.in_(_PENDING_SEND),
        )
        .group_by(Message.account_id)
        .all()
    )
    return {int(account_id): int(count or 0) for account_id, count in rows if account_id is not None}


def _prepared_count(db: Session, campaign_id: int) -> int:
    return int(
        db.query(func.count(RenderedMessage.id))
        .filter(
            RenderedMessage.campaign_id == campaign_id,
            RenderedMessage.ready_for_queue.is_(True),
        )
        .scalar()
        or 0
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(IRAN_TZ)


def _issue(code: str, message: str | None = None, account_id: int | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message or _PERSIAN.get(code, code),
        "account_id": account_id,
    }


def _map_execution_state(
    *,
    campaign_status: str,
    allowed: bool,
    warnings: list[dict[str, Any]],
    circuit_open: bool,
    safety_pause: dict[str, Any] | None,
    waiting_window: bool,
    waiting_capacity: bool,
    ready_to_resume: bool,
) -> str:
    status = str(campaign_status or "")
    if status == CampaignStatus.COMPLETED.value:
        return EXEC_COMPLETED
    if status == CampaignStatus.FAILED.value:
        return EXEC_FAILED
    if status == CampaignStatus.PAUSED.value:
        return EXEC_PAUSED_OPERATOR
    if status == CampaignStatus.RUNNING.value:
        if circuit_open or (safety_pause and not ready_to_resume):
            return EXEC_PAUSED_SAFETY
        if ready_to_resume:
            return EXEC_READY_TO_RESUME
        if waiting_window:
            return EXEC_WAITING_WINDOW
        if waiting_capacity:
            return EXEC_WAITING_CAPACITY
        return EXEC_RUNNING
    if not allowed:
        return EXEC_BLOCKED
    if waiting_window:
        return EXEC_WAITING_WINDOW
    if waiting_capacity:
        return EXEC_WAITING_CAPACITY
    if warnings:
        return EXEC_CAPACITY_WARNING
    return EXEC_READY


def _unknown_result(
    campaign_id: int,
    *,
    code: str,
    message: str,
    evaluated_iso: str,
    redis_ok: bool = False,
) -> CampaignPreflightResult:
    return CampaignPreflightResult(
        allowed_to_start=False,
        code=code,
        message=message,
        campaign_id=int(campaign_id),
        execution_safety_state=EXEC_BLOCKED,
        total_messages=0,
        ready_messages=0,
        blocked_messages=0,
        assigned_accounts=0,
        usable_accounts=0,
        blocked_accounts=0,
        temporary_accounts=0,
        immediate_capacity=None,
        estimated_today_capacity=None,
        estimated_completion_at=None,
        estimated_duration_seconds=None,
        timezone="Asia/Tehran",
        blockers=[_issue(code, message)],
        evaluated_at=evaluated_iso,
        redis_ok=redis_ok,
        capacity_confidence="unknown",
        limitations=["fail_closed"],
    )


async def evaluate_campaign_send_preflight(
    db: Session,
    campaign_id: int,
    now: datetime | None = None,
    *,
    redis: Any | None = None,
) -> CampaignPreflightResult:
    """Aggregate campaign safety/capacity. Fail closed when Redis cannot be read."""
    evaluated = policy_now(clock=now)
    evaluated_iso = evaluated.isoformat()

    try:
        campaign = db.query(Campaign).filter(Campaign.id == int(campaign_id)).first()
    except SQLAlchemyError as exc:
        logger.warning(
            "event=campaign_preflight_db_failure campaign_id=%s error=%s",
            campaign_id,
            type(exc).__name__,
        )
        log_campaign_safety_event(
            "campaign_preflight_blocked",
            campaign_id=int(campaign_id),
            code=CAMPAIGN_DEPENDENCY_ERROR,
        )
        return _unknown_result(
            int(campaign_id),
            code=CAMPAIGN_DEPENDENCY_ERROR,
            message=_PERSIAN[CAMPAIGN_DEPENDENCY_ERROR],
            evaluated_iso=evaluated_iso,
            redis_ok=True,
        )

    if campaign is None:
        return _unknown_result(
            int(campaign_id),
            code=CAMPAIGN_CONFIGURATION_INVALID,
            message="کمپین پیدا نشد.",
            evaluated_iso=evaluated_iso,
            redis_ok=True,
        )

    try:
        status_counts = _status_counts(db, campaign.id)
        staged = _staged_counts(db, campaign.id)
        assigned = _assignment_remaining(db, campaign.id)
        prepared = _prepared_count(db, campaign.id)
        links = (
            db.query(CampaignAccount)
            .filter(CampaignAccount.campaign_id == campaign.id)
            .order_by(CampaignAccount.priority.asc())
            .all()
        )
        manual = bool(links)
        sender_ids = [int(link.account_id) for link in links if link.enabled] if manual else list(assigned)
        accounts_by_id: dict[int, Account] = {}
        wanted = set(sender_ids) | set(assigned)
        if wanted:
            for acc in db.query(Account).filter(Account.id.in_(wanted)).all():
                accounts_by_id[int(acc.id)] = acc
        windows = load_send_windows(db) if campaign.platform == PlatformType.RUBIKA else []
    except SQLAlchemyError as exc:
        logger.warning(
            "event=campaign_preflight_db_failure campaign_id=%s error=%s",
            campaign.id,
            type(exc).__name__,
        )
        return _unknown_result(
            campaign.id,
            code=CAMPAIGN_DEPENDENCY_ERROR,
            message=_PERSIAN[CAMPAIGN_DEPENDENCY_ERROR],
            evaluated_iso=evaluated_iso,
            redis_ok=True,
        )

    total_recipients = sum(status_counts.values())
    pending_total = sum(status_counts.get(s.value, 0) for s in _PENDING_SEND)
    delivered = status_counts.get(SendStatus.DELIVERED.value, 0) + status_counts.get(
        SendStatus.READ.value, 0
    )
    failed_retryable = status_counts.get(SendStatus.FAILED_RETRYABLE.value, 0)
    failed_permanent = status_counts.get(SendStatus.FAILED_PERMANENT.value, 0)
    ready_staged = int(staged.get(StagedQueueItemStatus.READY.value, 0))
    queued_staged = int(staged.get(StagedQueueItemStatus.QUEUED.value, 0))
    pushing_staged = int(staged.get(StagedQueueItemStatus.PUSHING.value, 0))
    remaining_assigned = sum(assigned.values())
    remaining = remaining_assigned or pending_total or ready_staged

    progress = CampaignProgressCounts(
        total=total_recipients,
        prepared=prepared,
        pending=int(status_counts.get(SendStatus.PENDING.value, 0)),
        ready=ready_staged,
        queued=queued_staged,
        in_flight=queued_staged + pushing_staged,
        delivered=delivered,
        failed_retryable=failed_retryable,
        failed_permanent=failed_permanent,
    )

    warnings: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    code = CAMPAIGN_READY
    allowed = True

    if campaign.platform != PlatformType.RUBIKA:
        if remaining <= 0 and prepared <= 0 and ready_staged <= 0:
            allowed = False
            code = CAMPAIGN_NO_MESSAGES
            blockers.append(_issue(code))
        return CampaignPreflightResult(
            allowed_to_start=allowed,
            code=code,
            message=_PERSIAN.get(code, code),
            campaign_id=campaign.id,
            execution_safety_state=_map_execution_state(
                campaign_status=campaign.status,
                allowed=allowed,
                warnings=warnings,
                circuit_open=False,
                safety_pause=None,
                waiting_window=False,
                waiting_capacity=False,
                ready_to_resume=False,
            ),
            total_messages=total_recipients,
            ready_messages=ready_staged,
            blocked_messages=0,
            assigned_accounts=len(assigned) or len(sender_ids),
            usable_accounts=len(assigned) or len(sender_ids),
            blocked_accounts=0,
            temporary_accounts=0,
            immediate_capacity=ready_staged,
            estimated_today_capacity=None,
            estimated_completion_at=None,
            estimated_duration_seconds=None,
            timezone="Asia/Tehran",
            warnings=warnings,
            blockers=blockers,
            progress=asdict(progress),
            sender_selection_mode="manual" if manual else "automatic",
            evaluated_at=evaluated_iso,
            redis_ok=True,
            limitations=["non_rubika_capacity_not_modeled"],
            capacity_confidence="not_applicable",
        )

    try:
        delivery_mode = resolve_rubika_delivery_mode()
    except ValueError:
        allowed = False
        code = CAMPAIGN_CONFIGURATION_INVALID
        blockers.append(_issue(code))
        log_campaign_safety_event(
            "campaign_preflight_blocked", campaign_id=campaign.id, code=code
        )
        return CampaignPreflightResult(
            allowed_to_start=False,
            code=code,
            message=_PERSIAN[code],
            campaign_id=campaign.id,
            execution_safety_state=EXEC_BLOCKED,
            total_messages=total_recipients,
            ready_messages=ready_staged,
            blocked_messages=remaining,
            assigned_accounts=0,
            usable_accounts=0,
            blocked_accounts=0,
            temporary_accounts=0,
            immediate_capacity=None,
            estimated_today_capacity=None,
            estimated_completion_at=None,
            estimated_duration_seconds=None,
            timezone="Asia/Tehran",
            blockers=blockers,
            progress=asdict(progress),
            evaluated_at=evaluated_iso,
            redis_ok=True,
        )

    applies_quota = delivery_mode == RUBIKA_MODE_USER_ACCOUNT
    from core_engine.services.redis_client import get_redis_client, reset_redis_client
    from core_engine.services.rubika_circuit import get_circuit_snapshot
    from core_engine.services.rubika_health import (
        build_health_snapshot,
        get_quarantine_meta,
        is_account_quarantined,
    )
    from core_engine.services.rubika_quota import read_quota_snapshot
    from workers.config import get_worker_settings

    settings = get_worker_settings()
    try:
        client = redis if redis is not None else get_redis_client()
        await client.ping()
        redis_client = client
    except Exception as exc:  # noqa: BLE001 — fail closed
        logger.warning(
            "event=campaign_preflight_redis_failure campaign_id=%s error=%s",
            campaign.id,
            type(exc).__name__,
        )
        if redis is not None:
            log_campaign_safety_event(
                "campaign_preflight_blocked",
                campaign_id=campaign.id,
                code=CAMPAIGN_CAPACITY_UNKNOWN,
                extra={"error": type(exc).__name__},
            )
            return _unknown_result(
                campaign.id,
                code=CAMPAIGN_CAPACITY_UNKNOWN,
                message=_PERSIAN[CAMPAIGN_CAPACITY_UNKNOWN],
                evaluated_iso=evaluated_iso,
                redis_ok=False,
            )
        try:
            reset_redis_client()
            redis_client = get_redis_client()
            await redis_client.ping()
        except Exception as exc2:  # noqa: BLE001
            log_campaign_safety_event(
                "campaign_preflight_blocked",
                campaign_id=campaign.id,
                code=CAMPAIGN_CAPACITY_UNKNOWN,
                extra={"error": type(exc2).__name__},
            )
            return _unknown_result(
                campaign.id,
                code=CAMPAIGN_CAPACITY_UNKNOWN,
                message=_PERSIAN[CAMPAIGN_CAPACITY_UNKNOWN],
                evaluated_iso=evaluated_iso,
                redis_ok=False,
            )

    try:
        circuit = await get_circuit_snapshot(
            redis_client,
            probe_budget=int(settings.RUBIKA_CIRCUIT_PROBE_BUDGET),
            window_seconds=int(settings.RUBIKA_CIRCUIT_WINDOW_SECONDS),
            clock=evaluated,
        )
        circuit_state = circuit.state
        safety_pause = await get_campaign_safety_pause(redis_client, campaign.id)
    except Exception as exc:  # noqa: BLE001
        log_campaign_safety_event(
            "campaign_preflight_blocked",
            campaign_id=campaign.id,
            code=CAMPAIGN_CAPACITY_UNKNOWN,
            extra={"error": type(exc).__name__},
        )
        return _unknown_result(
            campaign.id,
            code=CAMPAIGN_CAPACITY_UNKNOWN,
            message=_PERSIAN[CAMPAIGN_CAPACITY_UNKNOWN],
            evaluated_iso=evaluated_iso,
            redis_ok=False,
        )

    circuit_open = circuit_state == "open"
    phase = current_window_phase(windows, evaluated) if applies_quota else "n/a"
    window_open = True if not applies_quota else phase is not None
    next_window = next_window_start(windows, evaluated) if applies_quota and not window_open else None

    planning_ids = list(dict.fromkeys([*assigned.keys(), *sender_ids]))
    if manual:
        planning_ids = [int(link.account_id) for link in links if link.enabled]
        for aid in assigned:
            if aid not in planning_ids:
                planning_ids.append(aid)

    if not planning_ids:
        allowed = False
        code = CAMPAIGN_NO_SENDERS
        blockers.append(_issue(code))

    cfg_daily = int(settings.RUBIKA_DAILY_SEND_CAP)
    cfg_hourly = int(settings.RUBIKA_HOURLY_SEND_CAP)
    cfg_min = int(settings.RUBIKA_MIN_SEND_DELAY_SECONDS)
    cfg_max = int(settings.RUBIKA_MAX_SEND_DELAY_SECONDS)

    quarantined_count = 0
    inputs: list[AccountCapacityInput] = []
    for account_id in planning_ids:
        account = accounts_by_id.get(account_id)
        assigned_n = int(assigned.get(account_id, 0))
        block_code: str | None = None
        health_state = None
        readiness_code = None
        lifecycle = None
        remaining_daily = None
        remaining_hourly = None
        daily_cap = None
        hourly_cap = None
        next_allowed = None
        cooldown_until = None
        label = account.label if account is not None else None

        if account is None:
            block_code = "ACCOUNT_MISSING"
            inputs.append(
                AccountCapacityInput(
                    account_id=account_id,
                    assigned_remaining=assigned_n,
                    label=label,
                    block_code=block_code,
                    delivery_mode=delivery_mode,
                )
            )
            continue

        readiness = evaluate_account_session_readiness(
            db, account, rubika_delivery_mode=delivery_mode
        )
        readiness_code = readiness.code
        if not readiness.ready:
            block_code = readiness.code
        try:
            quarantined = await is_account_quarantined(redis_client, account_id)
            qmeta = await get_quarantine_meta(redis_client, account_id) if quarantined else None
            health = await build_health_snapshot(
                redis_client,
                account,
                session_ready=readiness.ready,
                clock=evaluated,
            )
            health_state = health.health_state
            snap = await read_quota_snapshot(redis_client, account_id, clock=evaluated)
        except Exception as exc:  # noqa: BLE001
            log_campaign_safety_event(
                "campaign_preflight_blocked",
                campaign_id=campaign.id,
                code=CAMPAIGN_CAPACITY_UNKNOWN,
                extra={"error": type(exc).__name__, "account_id": account_id},
            )
            return _unknown_result(
                campaign.id,
                code=CAMPAIGN_CAPACITY_UNKNOWN,
                message=_PERSIAN[CAMPAIGN_CAPACITY_UNKNOWN],
                evaluated_iso=evaluated_iso,
                redis_ok=False,
            )

        if quarantined:
            quarantined_count += 1
            block_code = "ACCOUNT_QUARANTINED"
            if qmeta and qmeta.get("until"):
                cooldown_until = _parse_dt(str(qmeta.get("until")))
        elif account.status == AccountStatus.BANNED:
            block_code = "ACCOUNT_BANNED"
        elif account.status == AccountStatus.REQUIRES_LOGIN:
            block_code = "ACCOUNT_REQUIRES_LOGIN"
        elif account.status == AccountStatus.RESTING:
            block_code = "ACCOUNT_DISABLED"
        elif account.status != AccountStatus.ACTIVE:
            block_code = "ACCOUNT_SUSPENDED"
        elif circuit_open:
            block_code = "RUBIKA_CIRCUIT_OPEN"

        lifecycle_state = resolve_rubika_lifecycle(
            account,
            cooldown_active=bool(snap.cooldown_until),
            throttle_active=snap.throttle_active,
            clock=evaluated,
        )
        lifecycle = lifecycle_state.value
        limits = resolve_effective_limits(
            lifecycle_state,
            configured_daily_cap=cfg_daily,
            configured_hourly_cap=cfg_hourly,
            configured_min_interval=cfg_min,
            configured_max_interval=cfg_max,
            jitter_enabled=bool(settings.RUBIKA_JITTER_ENABLED),
        )
        if applies_quota:
            daily_cap = limits.daily_cap
            hourly_cap = limits.hourly_cap
            remaining_daily = max(0, limits.daily_cap - int(snap.sent_today))
            remaining_hourly = max(0, limits.hourly_cap - int(snap.sent_this_hour))
            if snap.cooldown_until:
                cooldown_until = _parse_dt(snap.cooldown_until)
                if block_code is None:
                    block_code = "COOLDOWN_ACTIVE"
            if snap.throttle_active and block_code is None:
                block_code = "ACCOUNT_THROTTLED"
            if snap.delay_ttl_seconds > 0 and block_code is None:
                block_code = "MIN_INTERVAL_ACTIVE"
                next_allowed = evaluated + timedelta(seconds=int(snap.delay_ttl_seconds))
            if remaining_daily <= 0 and block_code is None:
                block_code = "DAILY_CAP_REACHED"
            if remaining_hourly <= 0 and block_code is None:
                block_code = "HOURLY_CAP_REACHED"
            if not window_open and block_code is None:
                block_code = "OUTSIDE_SEND_WINDOW"

        inputs.append(
            AccountCapacityInput(
                account_id=account_id,
                assigned_remaining=assigned_n,
                label=label,
                health=health_state,
                readiness=readiness_code,
                lifecycle=lifecycle,
                remaining_daily=remaining_daily,
                remaining_hourly=remaining_hourly,
                daily_cap=daily_cap,
                hourly_cap=hourly_cap,
                min_interval_seconds=cfg_min,
                next_allowed_at=next_allowed,
                cooldown_until=cooldown_until,
                window_open=window_open if applies_quota else True,
                applies_quota=applies_quota,
                block_code=block_code,
                delivery_mode=delivery_mode,
            )
        )

    aggregate: CampaignCapacityAggregate = aggregate_campaign_capacity(
        inputs, now=evaluated, windows=windows
    )

    waiting_window = applies_quota and not window_open and aggregate.usable_now == 0
    waiting_capacity = (
        aggregate.usable_now == 0
        and aggregate.temporary > 0
        and aggregate.blocked == 0
        and not circuit_open
    )
    all_quarantined = bool(planning_ids) and quarantined_count == len(planning_ids)
    all_hard_blocked = bool(planning_ids) and aggregate.blocked == len(planning_ids)

    if remaining <= 0 and prepared <= 0 and ready_staged <= 0:
        allowed = False
        code = CAMPAIGN_NO_MESSAGES
        blockers.append(_issue(code))
    elif campaign.status == CampaignStatus.DRAFT.value and prepared <= 0 and ready_staged <= 0:
        allowed = False
        code = CAMPAIGN_NOT_PREPARED
        blockers.append(_issue(code))
    elif not planning_ids:
        allowed = False
        code = CAMPAIGN_NO_SENDERS
        blockers.append(_issue(code))
    elif circuit_open:
        allowed = False
        code = CAMPAIGN_CIRCUIT_OPEN
        blockers.append(_issue(code))
        log_campaign_safety_event(
            "campaign_preflight_blocked", campaign_id=campaign.id, code=code
        )
    elif all_quarantined:
        allowed = False
        code = CAMPAIGN_ALL_ACCOUNTS_QUARANTINED
        blockers.append(_issue(code))
        log_campaign_safety_event(
            "campaign_preflight_blocked", campaign_id=campaign.id, code=code
        )
    elif all_hard_blocked or (aggregate.usable_now == 0 and aggregate.temporary == 0 and planning_ids):
        allowed = False
        code = CAMPAIGN_SENDER_BLOCKED
        blockers.append(_issue(code))
        log_campaign_safety_event(
            "campaign_preflight_blocked", campaign_id=campaign.id, code=code
        )
    elif waiting_window:
        code = CAMPAIGN_OUTSIDE_SEND_WINDOW
        warnings.append(_issue(code))
        log_campaign_safety_event(
            "campaign_waiting_window",
            campaign_id=campaign.id,
            code=code,
            extra={"next_window_start": next_window.isoformat() if next_window else None},
        )
    elif waiting_capacity:
        code = CAMPAIGN_INSUFFICIENT_CAPACITY
        warnings.append(
            _issue(
                code,
                "ظرفیت فعلی تمام شده است؛ ارسال پس از تمدید سهمیه ادامه می‌یابد.",
            )
        )
        log_campaign_safety_event(
            "campaign_waiting_capacity", campaign_id=campaign.id, code=code
        )
        allowed = True
    else:
        code = CAMPAIGN_READY

    if campaign.status == CampaignStatus.RUNNING.value and allowed:
        warnings.append(_issue(CAMPAIGN_ALREADY_RUNNING))

    if (
        aggregate.estimated_today_capacity is not None
        and remaining > aggregate.estimated_today_capacity
    ):
        warnings.append(_issue("CAMPAIGN_MULTI_DAY", MULTI_DAY_WARNING))
        log_campaign_safety_event(
            "campaign_capacity_warning",
            campaign_id=campaign.id,
            code="CAMPAIGN_MULTI_DAY",
            extra={
                "remaining": remaining,
                "today_capacity": aggregate.estimated_today_capacity,
            },
        )

    mixed_blocked = aggregate.blocked
    if mixed_blocked and aggregate.usable_now > 0:
        warnings.append(
            _issue(
                "CAMPAIGN_PARTIAL_SENDERS",
                f"{mixed_blocked} اکانت از {aggregate.assigned_accounts} اکانت انتخاب‌شده فعلاً قابل ارسال نیستند.",
            )
        )

    for plan in aggregate.accounts:
        if plan.is_bottleneck:
            warnings.append(
                _issue(
                    "CAMPAIGN_ASSIGNMENT_BOTTLENECK",
                    (
                        f"اکانت {plan.account_id} گلوگاه ظرفیت است "
                        f"(تخصیص {plan.assigned_remaining}، ظرفیت امروز {plan.today_capacity})."
                    ),
                    account_id=plan.account_id,
                )
            )

    progress.waiting_window = remaining if waiting_window else 0
    progress.waiting_capacity = (
        sum(p.assigned_remaining for p in aggregate.accounts if p.is_temporary)
        if waiting_capacity
        else 0
    )
    progress.blocked_sender = sum(
        p.assigned_remaining for p in aggregate.accounts if p.is_hard_blocked
    )

    ready_to_resume = (
        campaign.status == CampaignStatus.RUNNING.value
        and not circuit_open
        and bool(safety_pause)
        and aggregate.usable_now > 0
    )

    completion = aggregate.completion
    exec_state = _map_execution_state(
        campaign_status=campaign.status,
        allowed=allowed,
        warnings=warnings,
        circuit_open=circuit_open,
        safety_pause=safety_pause,
        waiting_window=waiting_window,
        waiting_capacity=waiting_capacity,
        ready_to_resume=ready_to_resume,
    )

    account_rows = []
    for plan in aggregate.accounts:
        account_rows.append(
            {
                "account_id": plan.account_id,
                "label": plan.label,
                "health": plan.health,
                "readiness": plan.readiness,
                "lifecycle": plan.lifecycle,
                "assigned": plan.assigned_remaining,
                "daily_remaining": plan.remaining_daily,
                "hourly_remaining": plan.remaining_hourly,
                "next_allowed_at": plan.next_allowed_at.isoformat() if plan.next_allowed_at else None,
                "window_state": "open" if plan.window_open else "closed",
                "cooldown_until": plan.cooldown_until.isoformat() if plan.cooldown_until else None,
                "eligible_now": plan.eligible_now,
                "block_code": plan.block_code,
                "bottleneck": plan.is_bottleneck,
                "reason": plan.bottleneck_reason,
                "delivery_mode": plan.delivery_mode,
            }
        )

    if allowed and warnings:
        log_campaign_safety_event(
            "campaign_capacity_warning",
            campaign_id=campaign.id,
            code=code,
            extra={"warning_count": len(warnings)},
        )

    primary_message = _PERSIAN.get(code, code)
    if code not in _PERSIAN and warnings:
        primary_message = str(warnings[0].get("message") or code)

    return CampaignPreflightResult(
        allowed_to_start=allowed,
        code=code,
        message=primary_message,
        campaign_id=campaign.id,
        execution_safety_state=exec_state,
        total_messages=total_recipients,
        ready_messages=ready_staged,
        blocked_messages=progress.blocked_sender,
        assigned_accounts=aggregate.assigned_accounts,
        usable_accounts=aggregate.usable_now,
        blocked_accounts=aggregate.blocked,
        temporary_accounts=aggregate.temporary,
        immediate_capacity=aggregate.immediate_capacity,
        estimated_today_capacity=aggregate.estimated_today_capacity,
        estimated_hourly_capacity=aggregate.estimated_hourly_capacity,
        estimated_completion_at=(
            completion.estimated_completion_at.isoformat()
            if completion.estimated_completion_at is not None
            else None
        ),
        estimated_duration_seconds=completion.estimated_duration_seconds,
        timezone="Asia/Tehran",
        warnings=warnings,
        blockers=blockers,
        accounts=account_rows,
        progress=asdict(progress),
        sender_selection_mode="manual" if manual else "automatic",
        delivery_mode=delivery_mode,
        circuit_state=circuit_state,
        next_window_start=next_window.isoformat() if next_window else None,
        resume_policy="operator",
        ready_to_resume=ready_to_resume,
        capacity_confidence=aggregate.today_confidence,
        limitations=list(completion.limitations),
        evaluated_at=evaluated_iso,
        redis_ok=True,
    )
