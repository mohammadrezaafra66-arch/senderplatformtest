"""Central campaign-level send preflight (Phase 6).

Planning only: never consumes quota, never reassigns senders, never mutates
frozen final_text. Per-account sendability is evaluated by the authoritative
``evaluate_rubika_send_preflight`` engine (same codes as transport). Campaign
layer adds assignment/capacity aggregation and campaign-only codes such as
``CAMPAIGN_NOT_PREPARED``.
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
from core_engine.services.account_message_limits import remaining_for_limit
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

# Post-R10 production safety codes (also defined in campaign_production_guards).
INVALID_RECIPIENT_PHONE = "INVALID_RECIPIENT_PHONE"
UNRESOLVED_TEMPLATE_PLACEHOLDER = "UNRESOLVED_TEMPLATE_PLACEHOLDER"
INVALID_MESSAGE_ASSIGNMENT = "INVALID_MESSAGE_ASSIGNMENT"
DUPLICATE_RECIPIENT_ASSIGNMENT = "DUPLICATE_RECIPIENT_ASSIGNMENT"
CAMPAIGN_SEND_LIMIT_REACHED = "CAMPAIGN_SEND_LIMIT_REACHED"
MESSAGE_ALREADY_SENT = "MESSAGE_ALREADY_SENT"
NO_WORKER_CONSUMER = "NO_WORKER_CONSUMER"
CONTROLLED_PRODUCTION_APPROVAL_REQUIRED = "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"

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
    INVALID_RECIPIENT_PHONE: "یک یا چند گیرنده شماره روبیکای معتبر ندارند.",
    UNRESOLVED_TEMPLATE_PLACEHOLDER: "متن نهایی هنوز placeholder حل‌نشده دارد.",
    INVALID_MESSAGE_ASSIGNMENT: "تخصیص فرستنده پیام نامعتبر است.",
    DUPLICATE_RECIPIENT_ASSIGNMENT: "گیرنده تکراری در پیام‌های آماده‌شده وجود دارد.",
    CAMPAIGN_SEND_LIMIT_REACHED: "سقف تعداد ارسال کمپین رد شده است.",
    MESSAGE_ALREADY_SENT: "پیام قبلاً با موفقیت ارسال شده است.",
    NO_WORKER_CONSUMER: "پوشش worker برای حداقل یک اکانت اختصاص‌یافته فعال نیست.",
    CONTROLLED_PRODUCTION_APPROVAL_REQUIRED: (
        "برای شروع ارسال واقعی، تأیید نهایی اپراتور لازم است."
    ),
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
    redis_available: bool = True
    capacity_known: bool = True
    ready_accounts: int = 0
    execution_usable_accounts: int = 0
    campaign_eligible_accounts: int = 0
    authenticated_accounts: int = 0
    worker_ready_accounts: int = 0
    assignment_materialized: bool = False
    capacity_applicable: bool = False
    # Post-R10 production observability
    total_recipients: int = 0
    valid_recipient_count: int = 0
    invalid_recipient_count: int = 0
    prepared_messages: int = 0
    campaign_prepared: bool = False
    queued_messages: int = 0
    sending_messages: int = 0
    success_messages: int = 0
    failed_retryable: int = 0
    failed_permanent: int = 0
    worker_coverage_accounts: int = 0
    block_code: str | None = None
    block_details: list[dict[str, Any]] = field(default_factory=list)
    preparation_ready: bool = True
    preparation_blockers: list[dict[str, Any]] = field(default_factory=list)
    technical_ready: bool = False
    controlled_production_enabled: bool = False
    controlled_production_confirmation_required: bool = False
    allowed_to_start_after_confirmation: bool = False
    controlled_production_max_messages: int | None = None
    effective_cap: int | None = None
    unlimited: bool = True
    controlled_production_label: str | None = None

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
    total_messages: int = 0,
    assigned_accounts: int = 0,
    progress: dict[str, int] | None = None,
    blockers: list[dict[str, Any]] | None = None,
) -> CampaignPreflightResult:
    """Fail-closed placeholder for DB/config failures only.

    Redis unavailability after Postgres facts are loaded must not use this
    helper: it would zero real CampaignAccount / recipient counts.
    """
    issues = blockers if blockers is not None else [_issue(code, message)]
    return CampaignPreflightResult(
        allowed_to_start=False,
        code=code,
        message=message,
        campaign_id=int(campaign_id),
        execution_safety_state=EXEC_BLOCKED,
        total_messages=int(total_messages),
        ready_messages=0,
        blocked_messages=0,
        assigned_accounts=int(assigned_accounts),
        usable_accounts=0,
        blocked_accounts=0,
        temporary_accounts=0,
        immediate_capacity=None,
        estimated_today_capacity=None,
        estimated_completion_at=None,
        estimated_duration_seconds=None,
        timezone="Asia/Tehran",
        blockers=issues,
        progress=progress or {},
        evaluated_at=evaluated_iso,
        redis_ok=redis_ok,
        redis_available=redis_ok,
        capacity_known=False,
        capacity_confidence="unknown",
        limitations=["fail_closed"],
    )


def _pg_only_capacity_input(
    *,
    account_id: int,
    assigned_n: int,
    label: str | None,
    readiness_code: str | None,
    session_ready: bool,
    delivery_mode: str | None,
    window_open: bool,
    applies_quota: bool,
    account: Any | None = None,
) -> AccountCapacityInput:
    """Build a capacity row from Postgres only. Never invent Redis quota."""
    if not session_ready:
        block_code = readiness_code or "SESSION_INVALID"
    else:
        block_code = "NO_WORKER_CONSUMER"
    daily_cap = getattr(account, "daily_message_limit", None) if account is not None else None
    hourly_cap = getattr(account, "hourly_message_limit", None) if account is not None else None
    return AccountCapacityInput(
        account_id=account_id,
        assigned_remaining=assigned_n,
        label=label,
        health=None,
        readiness=readiness_code,
        lifecycle=None,
        remaining_daily=None,
        remaining_hourly=None,
        daily_cap=daily_cap,
        hourly_cap=hourly_cap,
        daily_unlimited=daily_cap is None,
        hourly_unlimited=hourly_cap is None,
        quota_known=False,
        window_open=window_open,
        applies_quota=applies_quota,
        block_code=block_code,
        delivery_mode=delivery_mode,
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

    if getattr(campaign, "archived_at", None) is not None:
        return _unknown_result(
            int(campaign_id),
            code="CAMPAIGN_ARCHIVED",
            message="این کمپین آرشیو شده و قابل اجرا نیست.",
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
    assignment_materialized = bool(
        assigned
        or prepared > 0
        or ready_staged > 0
        or queued_staged > 0
        or pushing_staged > 0
    )
    capacity_applicable = remaining > 0 and bool(assigned)

    progress = CampaignProgressCounts(
        total=total_recipients,
        prepared=prepared,
        pending=int(status_counts.get(SendStatus.PENDING.value, 0)),
        ready=ready_staged,
        queued=queued_staged,
        in_flight=int(
            db.query(func.count(StagedQueueItem.id))
            .join(
                CampaignRecipient,
                (CampaignRecipient.campaign_id == StagedQueueItem.campaign_id)
                & (CampaignRecipient.contact_id == StagedQueueItem.contact_id),
            )
            .filter(
                StagedQueueItem.campaign_id == campaign.id,
                StagedQueueItem.status.in_(
                    (
                        StagedQueueItemStatus.PUSHING.value,
                        StagedQueueItemStatus.QUEUED.value,
                    )
                ),
                CampaignRecipient.send_status.in_(_PENDING_SEND),
            )
            .scalar()
            or 0
        ),
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
    )
    from core_engine.services.rubika_quota import read_quota_snapshot
    from workers.config import get_worker_settings

    settings = get_worker_settings()
    redis_ok = True
    redis_client = None
    try:
        client = redis if redis is not None else get_redis_client()
        await client.ping()
        redis_client = client
    except Exception as exc:  # noqa: BLE001 — fail closed on start, keep DB facts
        logger.warning(
            "event=campaign_preflight_redis_failure campaign_id=%s error=%s",
            campaign.id,
            type(exc).__name__,
        )
        if redis is None:
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
                redis_ok = False
                redis_client = None
        else:
            log_campaign_safety_event(
                "campaign_preflight_blocked",
                campaign_id=campaign.id,
                code=CAMPAIGN_CAPACITY_UNKNOWN,
                extra={"error": type(exc).__name__},
            )
            redis_ok = False
            redis_client = None

    circuit_state = None
    safety_pause = None
    if redis_ok and redis_client is not None:
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
            redis_ok = False
            redis_client = None
            circuit_state = None
            safety_pause = None

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

    cfg_min = int(settings.RUBIKA_MIN_SEND_DELAY_SECONDS)
    cfg_max = int(settings.RUBIKA_MAX_SEND_DELAY_SECONDS)

    quarantined_count = 0
    inputs: list[AccountCapacityInput] = []
    from core_engine.services.rubika_preflight import (
        ACCOUNT_QUARANTINED as PF_ACCOUNT_QUARANTINED,
        OUTSIDE_SEND_WINDOW as PF_OUTSIDE_SEND_WINDOW,
        READY as PF_READY,
        evaluate_rubika_send_preflight,
    )

    for account_id in planning_ids:
        account = accounts_by_id.get(account_id)
        assigned_n = int(assigned.get(account_id, 0))
        block_code: str | None = None
        health_state = None
        readiness_code = None
        lifecycle = None
        remaining_daily = None
        remaining_hourly = None
        daily_cap = getattr(account, "daily_message_limit", None) if account is not None else None
        hourly_cap = getattr(account, "hourly_message_limit", None) if account is not None else None
        daily_unlimited = daily_cap is None
        hourly_unlimited = hourly_cap is None
        quota_known = False
        next_allowed = None
        cooldown_until = None
        label = account.label if account is not None else None
        account_window_open = window_open if applies_quota else True

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

        pf = None
        snap = None
        use_pg_only = not redis_ok or redis_client is None
        if not use_pg_only:
            try:
                pf = await evaluate_rubika_send_preflight(
                    db,
                    account=account,
                    campaign_id=campaign.id,
                    delivery_mode=delivery_mode,
                    context="campaign_preflight",
                    redis=redis_client,
                    clock=evaluated,
                    consume_circuit_probe=False,
                )
            except Exception as exc:  # noqa: BLE001 — keep Postgres facts
                log_campaign_safety_event(
                    "campaign_preflight_blocked",
                    campaign_id=campaign.id,
                    code=CAMPAIGN_CAPACITY_UNKNOWN,
                    extra={"error": type(exc).__name__, "account_id": account_id},
                )
                redis_ok = False
                use_pg_only = True

        if use_pg_only:
            inputs.append(
                _pg_only_capacity_input(
                    account_id=account_id,
                    assigned_n=assigned_n,
                    label=label,
                    readiness_code=readiness_code,
                    session_ready=bool(readiness.ready),
                    delivery_mode=delivery_mode,
                    window_open=account_window_open,
                    applies_quota=applies_quota,
                    account=account,
                )
            )
            continue

        if pf.code != PF_READY:
            block_code = pf.code
        elif pf.code == PF_READY:
            # R5: session/policy READY is not enough — need live worker coverage.
            try:
                from workers.pool_health import has_active_worker_coverage

                covered = await has_active_worker_coverage(
                    redis_client, platform="rubika", account_id=account_id
                )
                if not covered:
                    block_code = "NO_WORKER_CONSUMER"
            except Exception:  # noqa: BLE001 — fail soft: treat as no coverage
                block_code = "NO_WORKER_CONSUMER"
        if block_code == PF_ACCOUNT_QUARANTINED:
            quarantined_count += 1
        if block_code == PF_OUTSIDE_SEND_WINDOW:
            account_window_open = False

        try:
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
            redis_ok = False
            inputs.append(
                _pg_only_capacity_input(
                    account_id=account_id,
                    assigned_n=assigned_n,
                    label=label,
                    readiness_code=readiness_code,
                    session_ready=bool(readiness.ready),
                    delivery_mode=delivery_mode,
                    window_open=account_window_open,
                    applies_quota=applies_quota,
                    account=account,
                )
            )
            continue

        if pf.details.get("until"):
            cooldown_until = _parse_dt(str(pf.details.get("until")))

        lifecycle_state = resolve_rubika_lifecycle(
            account,
            cooldown_active=bool(snap.cooldown_until),
            throttle_active=snap.throttle_active,
            clock=evaluated,
        )
        lifecycle = lifecycle_state.value
        limits = resolve_effective_limits(
            lifecycle_state,
            configured_daily_cap=int(settings.RUBIKA_DAILY_SEND_CAP),
            configured_hourly_cap=int(settings.RUBIKA_HOURLY_SEND_CAP),
            configured_min_interval=cfg_min,
            configured_max_interval=cfg_max,
            jitter_enabled=bool(settings.RUBIKA_JITTER_ENABLED),
        )
        min_interval_seconds = limits.min_interval_seconds
        if applies_quota:
            remaining_daily = remaining_for_limit(daily_cap, int(snap.sent_today))
            remaining_hourly = remaining_for_limit(hourly_cap, int(snap.sent_this_hour))
            quota_known = True
            if snap.cooldown_until and cooldown_until is None:
                cooldown_until = _parse_dt(snap.cooldown_until)
            if snap.delay_ttl_seconds > 0:
                next_allowed = evaluated + timedelta(seconds=int(snap.delay_ttl_seconds))
            policy = pf.details.get("policy") if isinstance(pf.details, dict) else None
            if isinstance(policy, dict):
                if "remaining_daily" in policy:
                    remaining_daily = policy.get("remaining_daily")
                    if remaining_daily is not None:
                        remaining_daily = max(0, int(remaining_daily))
                if "remaining_hourly" in policy:
                    remaining_hourly = policy.get("remaining_hourly")
                    if remaining_hourly is not None:
                        remaining_hourly = max(0, int(remaining_hourly))
                if "daily_unlimited" in policy:
                    daily_unlimited = bool(policy.get("daily_unlimited"))
                if "hourly_unlimited" in policy:
                    hourly_unlimited = bool(policy.get("hourly_unlimited"))
                if policy.get("daily_cap") is not None:
                    daily_cap = int(policy["daily_cap"])
                    daily_unlimited = False
                elif policy.get("daily_unlimited") is True:
                    daily_cap = None
                    daily_unlimited = True
                if policy.get("hourly_cap") is not None:
                    hourly_cap = int(policy["hourly_cap"])
                    hourly_unlimited = False
                elif policy.get("hourly_unlimited") is True:
                    hourly_cap = None
                    hourly_unlimited = True

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
                daily_unlimited=daily_unlimited,
                hourly_unlimited=hourly_unlimited,
                quota_known=quota_known,
                min_interval_seconds=min_interval_seconds,
                next_allowed_at=next_allowed,
                cooldown_until=cooldown_until,
                window_open=account_window_open,
                applies_quota=applies_quota,
                block_code=block_code,
                delivery_mode=delivery_mode,
            )
        )

    aggregate: CampaignCapacityAggregate = aggregate_campaign_capacity(
        inputs, now=evaluated, windows=windows
    )

    waiting_window = (
        redis_ok and applies_quota and not window_open and aggregate.usable_now == 0
    )
    waiting_capacity = (
        redis_ok
        and aggregate.usable_now == 0
        and aggregate.temporary > 0
        and aggregate.blocked == 0
        and not circuit_open
    )
    all_quarantined = bool(planning_ids) and quarantined_count == len(planning_ids)
    all_hard_blocked = bool(planning_ids) and aggregate.blocked == len(planning_ids)

    if not planning_ids:
        allowed = False
        if not any(b.get("code") == CAMPAIGN_NO_SENDERS for b in blockers):
            blockers.append(_issue(CAMPAIGN_NO_SENDERS))
        code = CAMPAIGN_NO_SENDERS
        if remaining <= 0 and prepared <= 0 and ready_staged <= 0:
            if not any(b.get("code") == CAMPAIGN_NO_MESSAGES for b in blockers):
                blockers.append(_issue(CAMPAIGN_NO_MESSAGES))
        elif campaign.status == CampaignStatus.DRAFT.value and prepared <= 0 and ready_staged <= 0:
            if not any(b.get("code") == CAMPAIGN_NOT_PREPARED for b in blockers):
                blockers.append(_issue(CAMPAIGN_NOT_PREPARED))
    elif remaining <= 0 and prepared <= 0 and ready_staged <= 0:
        allowed = False
        code = CAMPAIGN_NO_MESSAGES
        blockers.append(_issue(code))
    elif campaign.status == CampaignStatus.DRAFT.value and prepared <= 0 and ready_staged <= 0:
        allowed = False
        code = CAMPAIGN_NOT_PREPARED
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

    # Explicit worker-coverage gate (separate from account readiness).
    # NO_WORKER_CONSUMER is temporary for capacity math, but must block start.
    _HARD_SYSTEM_CODES = {
        CAMPAIGN_CIRCUIT_OPEN,
        CAMPAIGN_DEPENDENCY_ERROR,
        CAMPAIGN_CAPACITY_UNKNOWN,
    }
    coverage_missing = [
        plan.account_id
        for plan in aggregate.accounts
        if plan.block_code == "NO_WORKER_CONSUMER"
    ]
    if coverage_missing and redis_ok:
        allowed = False
        if code not in _HARD_SYSTEM_CODES:
            code = NO_WORKER_CONSUMER
        blockers.append(
            _issue(
                NO_WORKER_CONSUMER,
                "حداقل یک اکانت اختصاص‌یافته پوشش worker فعال ندارد.",
                account_id=coverage_missing[0],
            )
        )
        log_campaign_safety_event(
            "campaign_preflight_blocked",
            campaign_id=campaign.id,
            code=NO_WORKER_CONSUMER,
            extra={"account_ids": coverage_missing},
        )

    if not redis_ok:
        allowed = False
        if not any(b.get("code") == CAMPAIGN_CAPACITY_UNKNOWN for b in blockers):
            blockers.append(_issue(CAMPAIGN_CAPACITY_UNKNOWN))
        if code not in {
            CAMPAIGN_NO_SENDERS,
            CAMPAIGN_NO_MESSAGES,
            CAMPAIGN_NOT_PREPARED,
            CAMPAIGN_CONFIGURATION_INVALID,
            CAMPAIGN_DEPENDENCY_ERROR,
        }:
            code = CAMPAIGN_CAPACITY_UNKNOWN

    # --- Post-R10 production safety gates (fail closed; do not mutate) ---
    from core_engine.services.campaign_production_guards import (
        evaluate_send_limit,
        scan_assignment_consistency,
        scan_campaign_recipient_phones,
        scan_unresolved_placeholders_for_campaign,
    )

    valid_contact_ids, phone_issues = scan_campaign_recipient_phones(db, campaign)
    valid_recipient_count = len(valid_contact_ids)
    invalid_recipient_count = len(phone_issues)
    placeholder_issues = scan_unresolved_placeholders_for_campaign(db, campaign.id)
    assignment_issues = scan_assignment_consistency(db, campaign)
    limit_issue = evaluate_send_limit(
        db,
        campaign,
        prepared_or_ready_count=max(prepared, ready_staged, total_recipients),
    )
    if invalid_recipient_count > 0:
        blockers.extend([i.as_blocker() for i in phone_issues])
        allowed = False
        if code not in _HARD_SYSTEM_CODES:
            code = INVALID_RECIPIENT_PHONE
        log_campaign_safety_event(
            "campaign_preflight_blocked",
            campaign_id=campaign.id,
            code=INVALID_RECIPIENT_PHONE,
            extra={"invalid_recipient_count": invalid_recipient_count},
        )
    if placeholder_issues:
        blockers.extend([i.as_blocker() for i in placeholder_issues])
        allowed = False
        if code not in _HARD_SYSTEM_CODES:
            code = UNRESOLVED_TEMPLATE_PLACEHOLDER
        log_campaign_safety_event(
            "campaign_preflight_blocked",
            campaign_id=campaign.id,
            code=UNRESOLVED_TEMPLATE_PLACEHOLDER,
            extra={"count": len(placeholder_issues)},
        )
    if assignment_issues:
        blockers.extend([i.as_blocker() for i in assignment_issues])
        allowed = False
        if code not in _HARD_SYSTEM_CODES:
            code = str(assignment_issues[0].code)
        log_campaign_safety_event(
            "campaign_preflight_blocked",
            campaign_id=campaign.id,
            code=str(assignment_issues[0].code),
        )
    if limit_issue is not None:
        blockers.append(limit_issue.as_blocker())
        allowed = False
        if code not in _HARD_SYSTEM_CODES:
            code = CAMPAIGN_SEND_LIMIT_REACHED
        log_campaign_safety_event(
            "campaign_preflight_blocked",
            campaign_id=campaign.id,
            code=CAMPAIGN_SEND_LIMIT_REACHED,
            extra=limit_issue.details,
        )

    if not planning_ids:
        allowed = False
        if not any(b.get("code") == CAMPAIGN_NO_SENDERS for b in blockers):
            blockers.append(_issue(CAMPAIGN_NO_SENDERS))
        code = CAMPAIGN_NO_SENDERS

    if campaign.status == CampaignStatus.RUNNING.value and allowed:
        warnings.append(_issue(CAMPAIGN_ALREADY_RUNNING))

    if (
        capacity_applicable
        and aggregate.estimated_today_capacity is not None
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
        if capacity_applicable and plan.is_bottleneck:
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
    from core_engine.services.campaign_execution_labels import resolve_account_execution_blocker
    from core_engine.services.account_runtime_status import compute_all_account_runtime_statuses
    from core_engine.services.campaign_sender_eligibility import (
        evaluate_campaign_sender_eligibility,
        resolve_display_identity,
    )

    runtime_by_id: dict[int, Any] = {}
    if accounts_by_id:
        runtimes = compute_all_account_runtime_statuses(db, list(accounts_by_id.values()))
        runtime_by_id = {int(r.account_id): r for r in runtimes}

    campaign_prepared = not (
        campaign.status == CampaignStatus.DRAFT.value and prepared <= 0 and ready_staged <= 0
    )

    authenticated_accounts = 0
    worker_ready_accounts = 0
    for plan in aggregate.accounts:
        worker_coverage = False
        if redis_ok:
            worker_coverage = plan.block_code != "NO_WORKER_CONSUMER"
            if plan.block_code is None or plan.block_code == "READY":
                worker_coverage = True
            if plan.block_code == "NO_WORKER_CONSUMER":
                worker_coverage = False
            elif plan.account_ready_now and plan.block_code not in {None, "READY"}:
                worker_coverage = plan.block_code != "NO_WORKER_CONSUMER"
        account = accounts_by_id.get(plan.account_id)
        elig = None
        display_identity = plan.label
        if account is not None:
            elig = evaluate_campaign_sender_eligibility(
                db,
                account,
                campaign=campaign,
                runtime=runtime_by_id.get(int(plan.account_id)),
                capacity_block_code=plan.block_code,
                daily_remaining=plan.remaining_daily,
                hourly_remaining=plan.remaining_hourly,
                next_send_at=plan.next_allowed_at.isoformat() if plan.next_allowed_at else None,
                assigned=plan.assigned_remaining > 0,
            )
            display_identity = elig.display_identity
        else:
            display_identity = resolve_display_identity(
                account_id=int(plan.account_id),
                phone_number=None,
                label=plan.label,
            )
        next_allowed_iso = plan.next_allowed_at.isoformat() if plan.next_allowed_at else None
        exec_block_code, exec_block_label = resolve_account_execution_blocker(
            eligible_now=plan.eligible_now,
            account_ready_now=plan.account_ready_now,
            block_code=plan.block_code,
            assignment_materialized=assignment_materialized,
            campaign_prepared=campaign_prepared,
            next_allowed_at=next_allowed_iso,
        )
        if elig is not None and elig.execution_blocker_code:
            exec_block_code = elig.execution_blocker_code
            exec_block_label = elig.execution_blocker_label
        execution_ready = plan.eligible_now if plan.assigned_remaining > 0 else (
            elig.execution_ready if elig is not None else False
        )
        if not campaign_prepared or not redis_ok:
            execution_ready = False
        if elig is not None and elig.auth_ready:
            authenticated_accounts += 1
        if elig is not None and elig.worker_ready and worker_coverage:
            worker_ready_accounts += 1
        account_rows.append(
            {
                "account_id": plan.account_id,
                "label": plan.label,
                "display_identity": display_identity,
                "runtime_status": elig.runtime_status if elig else None,
                "runtime_status_label": elig.runtime_status_label if elig else None,
                "account_health_label": elig.runtime_status_label if elig else None,
                "campaign_eligible": elig.campaign_eligible if elig else False,
                "blocker_code": elig.blocker_code if elig else plan.block_code,
                "blocker_label": elig.blocker_label if elig else None,
                "execution_ready": execution_ready,
                "execution_blocker_code": exec_block_code,
                "execution_blocker_label": exec_block_label,
                "execution_status_label": (
                    exec_block_label
                    if exec_block_label
                    else ("آماده ارسال" if execution_ready else None)
                ),
                "health": plan.health,
                "readiness": plan.readiness,
                "lifecycle": plan.lifecycle,
                "assigned": plan.assigned_remaining,
                "assignment_state": (
                    "assigned"
                    if plan.assigned_remaining > 0
                    else ("unassigned" if assignment_materialized else "not_materialized")
                ),
                "account_ready_now": plan.account_ready_now,
                "worker_coverage": worker_coverage,
                "daily_remaining": plan.remaining_daily,
                "hourly_remaining": plan.remaining_hourly,
                "daily_unlimited": plan.daily_unlimited,
                "hourly_unlimited": plan.hourly_unlimited,
                "daily_cap": plan.daily_cap,
                "hourly_cap": plan.hourly_cap,
                "quota_known": plan.quota_known,
                "next_allowed_at": plan.next_allowed_at.isoformat() if plan.next_allowed_at else None,
                "window_state": "open" if plan.window_open else "closed",
                "cooldown_until": plan.cooldown_until.isoformat() if plan.cooldown_until else None,
                "eligible_now": bool(plan.eligible_now and redis_ok),
                "block_code": plan.block_code,
                "reason_code": exec_block_code or ("READY" if plan.eligible_now else None),
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

    worker_coverage_accounts = sum(
        1 for row in account_rows if row.get("worker_coverage") is True
    )
    queued_messages = queued_staged + pushing_staged
    sending_messages = status_counts.get(SendStatus.PROCESSING.value, 0) + status_counts.get(
        SendStatus.ACCEPTED_BY_WORKER.value, 0
    )
    block_details = list(blockers) if not allowed else []

    preparation_blockers: list[dict[str, Any]] = []
    preparation_ready = True
    if not campaign_prepared:
        from core_engine.services.campaign_preparation import evaluate_preparation_readiness

        preparation_blockers = evaluate_preparation_readiness(db, campaign)
        preparation_ready = len(preparation_blockers) == 0

    from core_engine.models import PlatformType as _PlatformType
    from core_engine.services.campaign_production_guards import (
        campaign_cap_view,
        controlled_production_enabled,
    )

    technical_ready = bool(allowed and code == CAMPAIGN_READY)
    cp_enabled = bool(controlled_production_enabled())
    cp_confirmation_required = bool(
        cp_enabled
        and campaign.platform == _PlatformType.RUBIKA
        and technical_ready
    )
    cap_view = campaign_cap_view(campaign)
    cp_max = cap_view["effective_cap"]
    cp_label = (
        "حالت ارسال کنترل‌شده فعال است — تأیید نهایی برای هر شروع لازم است."
        if cp_enabled and campaign.platform == _PlatformType.RUBIKA
        else None
    )
    allowed_after_confirmation = bool(technical_ready and cp_confirmation_required)

    if cp_confirmation_required:
        allowed = False
        code = CONTROLLED_PRODUCTION_APPROVAL_REQUIRED
        primary_message = _PERSIAN[CONTROLLED_PRODUCTION_APPROVAL_REQUIRED]
        if not any(
            b.get("code") == CONTROLLED_PRODUCTION_APPROVAL_REQUIRED for b in blockers
        ):
            blockers.append(
                _issue(
                    CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
                    primary_message,
                )
            )
        block_details = list(blockers)

    return CampaignPreflightResult(
        allowed_to_start=allowed,
        code=code,
        message=primary_message,
        campaign_id=campaign.id,
        execution_safety_state=exec_state,
        total_messages=total_recipients,
        ready_messages=ready_staged,
        blocked_messages=progress.blocked_sender,
        assigned_accounts=max(len(planning_ids), aggregate.assigned_accounts),
        # ``usable_accounts`` is the public/UI-facing readiness count.  It must
        # not collapse to zero before Message.account_id assignments exist.
        usable_accounts=aggregate.ready_now if redis_ok else 0,
        blocked_accounts=aggregate.blocked,
        temporary_accounts=aggregate.temporary,
        immediate_capacity=(
            None if not redis_ok else (aggregate.immediate_capacity if capacity_applicable else None)
        ),
        estimated_today_capacity=(
            None
            if not redis_ok
            else (aggregate.estimated_today_capacity if capacity_applicable else None)
        ),
        estimated_hourly_capacity=(
            None
            if not redis_ok
            else (aggregate.estimated_hourly_capacity if capacity_applicable else None)
        ),
        estimated_completion_at=(
            None
            if not redis_ok
            else (
                completion.estimated_completion_at.isoformat()
                if capacity_applicable and completion.estimated_completion_at is not None
                else None
            )
        ),
        estimated_duration_seconds=(
            None
            if not redis_ok
            else (completion.estimated_duration_seconds if capacity_applicable else None)
        ),
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
        capacity_confidence=(
            "unknown"
            if not redis_ok
            else (aggregate.today_confidence if capacity_applicable else "not_applicable")
        ),
        limitations=(
            (["redis_unavailable", "capacity_unknown"] if not redis_ok else [])
            + (
                list(completion.limitations)
                if capacity_applicable
                else ["assignment_not_materialized", "capacity_not_applicable_before_assignment"]
            )
        ),
        evaluated_at=evaluated_iso,
        redis_ok=redis_ok,
        redis_available=redis_ok,
        capacity_known=bool(redis_ok),
        ready_accounts=aggregate.ready_now if redis_ok else 0,
        execution_usable_accounts=aggregate.usable_now if redis_ok else 0,
        campaign_eligible_accounts=sum(
            1 for row in account_rows if row.get("campaign_eligible") is True
        ),
        authenticated_accounts=authenticated_accounts,
        worker_ready_accounts=worker_ready_accounts,
        assignment_materialized=assignment_materialized,
        capacity_applicable=capacity_applicable,
        total_recipients=total_recipients,
        valid_recipient_count=valid_recipient_count,
        invalid_recipient_count=invalid_recipient_count,
        prepared_messages=prepared,
        campaign_prepared=campaign_prepared,
        queued_messages=queued_messages,
        sending_messages=sending_messages,
        success_messages=delivered,
        failed_retryable=failed_retryable,
        failed_permanent=failed_permanent,
        worker_coverage_accounts=worker_coverage_accounts,
        block_code=(None if allowed else code),
        block_details=block_details,
        preparation_ready=preparation_ready,
        preparation_blockers=preparation_blockers,
        technical_ready=technical_ready,
        controlled_production_enabled=cp_enabled,
        controlled_production_confirmation_required=cp_confirmation_required,
        allowed_to_start_after_confirmation=allowed_after_confirmation,
        controlled_production_max_messages=cp_max,
        effective_cap=cap_view["effective_cap"],
        unlimited=bool(cap_view["unlimited"]),
        controlled_production_label=cp_label,
    )
