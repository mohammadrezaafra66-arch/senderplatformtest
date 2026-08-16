"""Central Rubika send preflight gate (Phase 2).

Invariant: no Rubika message transport may proceed unless this service
returns allowed=True. Connectors must call it before HTTP/rubpy send.

Reuses Phase 1 readiness (`evaluate_account_session_readiness`) — does not
reimplement session parsing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    CampaignAccount,
    PlatformType,
    RubikaAccountPool,
)
from core_engine.services.account_session_wiring import (
    ACCOUNT_BANNED,
    ACCOUNT_DISABLED,
    ACCOUNT_IDENTIFIER_MISSING,
    ACCOUNT_MISSING,
    ACCOUNT_REQUIRES_LOGIN,
    CONFIG_INVALID,
    READY,
    SESSION_DECRYPT_FAILED,
    SESSION_INVALID,
    SESSION_MISSING,
    USER_ACCOUNT_DISABLED,
    evaluate_account_session_readiness,
)
from core_engine.services.rubika_mode import (
    RUBIKA_MODE_BOT_API,
    RUBIKA_MODE_USER_ACCOUNT,
    resolve_rubika_delivery_mode,
    rubika_required_session_type,
)

logger = logging.getLogger("core_engine.services.rubika_preflight")

# Extra Phase-2 runtime codes (identity/mode/session reuse Phase 1 names).
WRONG_PLATFORM = "WRONG_PLATFORM"
DELIVERY_MODE_DISABLED = "DELIVERY_MODE_DISABLED"
ACCOUNT_NOT_IN_ALLOWED_POOL = "ACCOUNT_NOT_IN_ALLOWED_POOL"
CAMPAIGN_ACCOUNT_NOT_ALLOWED = "CAMPAIGN_ACCOUNT_NOT_ALLOWED"
COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
HOURLY_CAP_REACHED = "HOURLY_CAP_REACHED"
DAILY_CAP_REACHED = "DAILY_CAP_REACHED"
MIN_INTERVAL_ACTIVE = "MIN_INTERVAL_ACTIVE"
OUTSIDE_SEND_WINDOW = "OUTSIDE_SEND_WINDOW"
ACCOUNT_THROTTLED = "ACCOUNT_THROTTLED"
ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
REDIS_UNAVAILABLE = "REDIS_UNAVAILABLE"
UNKNOWN_PREFLIGHT_ERROR = "UNKNOWN_PREFLIGHT_ERROR"

# Codes that are safe to retry later without operator login/config change.
_RETRYABLE_CODES = frozenset({
    COOLDOWN_ACTIVE,
    HOURLY_CAP_REACHED,
    DAILY_CAP_REACHED,
    MIN_INTERVAL_ACTIVE,
    OUTSIDE_SEND_WINDOW,
    ACCOUNT_THROTTLED,
    REDIS_UNAVAILABLE,
    ACCOUNT_NOT_IN_ALLOWED_POOL,  # pool membership may change; treat as retryable
})

_PERSIAN_MESSAGES: dict[str, str] = {
    READY: "پیش‌پرواز روبیکا مجاز است.",
    ACCOUNT_MISSING: "اکانت پیدا نشد.",
    WRONG_PLATFORM: "اکانت متعلق به روبیکا نیست.",
    ACCOUNT_DISABLED: "اکانت در حالت استراحت/غیرفعال است و مجاز به ارسال نیست.",
    ACCOUNT_BANNED: "اکانت بن شده و ارسال مجاز نیست.",
    ACCOUNT_REQUIRES_LOGIN: "اکانت نیاز به ورود مجدد دارد.",
    ACCOUNT_IDENTIFIER_MISSING: "شناسه اکانت ناقص است.",
    SESSION_MISSING: "سشن روبیکا ثبت نشده است.",
    SESSION_INVALID: "سشن روبیکا نامعتبر است.",
    SESSION_DECRYPT_FAILED: "سشن روبیکا قابل رمزگشایی نیست.",
    CONFIG_INVALID: "پیکربندی حالت ارسال روبیکا نامعتبر است.",
    USER_ACCOUNT_DISABLED: "حالت user_account در پیکربندی غیرفعال است.",
    DELIVERY_MODE_DISABLED: "حالت ارسال روبیکا برای این مسیر غیرفعال است.",
    ACCOUNT_NOT_IN_ALLOWED_POOL: "اکانت در استخر فاز فعال روبیکا نیست.",
    CAMPAIGN_ACCOUNT_NOT_ALLOWED: "اکانت برای این کمپین مجاز نیست.",
    COOLDOWN_ACTIVE: "اکانت در دوره انتظار (cooldown) است.",
    HOURLY_CAP_REACHED: "سقف ارسال ساعتی اکانت پر شده است.",
    DAILY_CAP_REACHED: "سقف ارسال روزانه اکانت پر شده است.",
    MIN_INTERVAL_ACTIVE: "حداقل فاصله ارسال برای این اکانت هنوز فعال است.",
    OUTSIDE_SEND_WINDOW: "خارج از بازه زمانی ارسال روبیکا است.",
    ACCOUNT_THROTTLED: "اکانت در حالت محدودیت موقت (throttled) است.",
    ACCOUNT_SUSPENDED: "اکانت معلق است و ارسال مجاز نیست.",
    REDIS_UNAVAILABLE: "سرویس Redis در دسترس نیست؛ ارسال متوقف شد.",
    UNKNOWN_PREFLIGHT_ERROR: "خطای ناشناخته در پیش‌پرواز ارسال روبیکا.",
}


@dataclass(frozen=True, slots=True)
class RubikaPreflightResult:
    allowed: bool
    code: str
    message: str
    account_id: int | None = None
    delivery_mode: str | None = None
    session_type: str | None = None
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def worker_error_code(self) -> str:
        """Stable WorkerResult.error_code (snake_case, rubika_ prefixed)."""
        return f"rubika_{self.code.lower()}"


def _deny(
    *,
    code: str,
    message: str | None = None,
    account_id: int | None = None,
    delivery_mode: str | None = None,
    session_type: str | None = None,
    details: dict[str, Any] | None = None,
    retryable: bool | None = None,
) -> RubikaPreflightResult:
    return RubikaPreflightResult(
        allowed=False,
        code=code,
        message=message or _PERSIAN_MESSAGES.get(code, code),
        account_id=account_id,
        delivery_mode=delivery_mode,
        session_type=session_type,
        retryable=_RETRYABLE_CODES.__contains__(code) if retryable is None else retryable,
        details=details or {},
    )


def _allow(
    *,
    account_id: int,
    delivery_mode: str,
    session_type: str,
    details: dict[str, Any] | None = None,
) -> RubikaPreflightResult:
    return RubikaPreflightResult(
        allowed=True,
        code=READY,
        message=_PERSIAN_MESSAGES[READY],
        account_id=account_id,
        delivery_mode=delivery_mode,
        session_type=session_type,
        retryable=False,
        details=details or {},
    )


def log_rubika_preflight_denial(
    result: RubikaPreflightResult,
    *,
    context: str,
    campaign_id: int | str | None = None,
    message_id: int | str | None = None,
    recipient_type: str | None = None,
) -> None:
    """Structured denial log — never logs secrets/session material."""
    logger.warning(
        "event=rubika_preflight_denied context=%s account_id=%s campaign_id=%s "
        "message_id=%s mode=%s preflight_code=%s retryable=%s recipient_type=%s",
        context,
        result.account_id,
        campaign_id,
        message_id,
        result.delivery_mode,
        result.code,
        result.retryable,
        recipient_type,
    )


def preflight_to_worker_result(result: RubikaPreflightResult):
    """Map a denial to WorkerResult. Caller must not call transport."""
    from workers.payloads import WorkerResult

    status = "failed_retryable" if result.retryable else "failed_permanent"
    return WorkerResult(
        success=False,
        status=status,
        error_code=result.worker_error_code,
        error_message=result.message,
        retryable=result.retryable,
    )


def _parse_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _campaign_allows_account(
    db: Session,
    *,
    campaign_id: int,
    account_id: int,
) -> bool:
    """If campaign has manual CampaignAccount rows, account must be enabled in them.

    Empty relation set = Auto mode (any same-platform account may have been assigned
    at prepare time) — do not block here.
    """
    links = (
        db.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == campaign_id)
        .all()
    )
    if not links:
        return True
    return any(
        link.account_id == account_id and link.enabled for link in links
    )


def _account_in_phase_pool(db: Session, *, account_id: int, phase: str) -> bool:
    row = (
        db.query(RubikaAccountPool)
        .filter(
            RubikaAccountPool.account_id == account_id,
            RubikaAccountPool.phase == phase,
        )
        .first()
    )
    return row is not None


async def evaluate_rubika_send_preflight(
    db: Session,
    *,
    account: Account | None = None,
    account_id: int | None = None,
    delivery_mode: str | None = None,
    user_account_enabled: bool | None = None,
    campaign_id: int | str | None = None,
    context: str = "worker",
    redis: Any | None = None,
    hourly_cap: int | None = None,
    daily_cap: int | None = None,
    check_runtime_limits: bool | None = None,
    check_pool_membership: bool | None = None,
    check_campaign_assignment: bool | None = None,
    check_send_window: bool | None = None,
    clock: Any | None = None,
    rng: Any | None = None,
) -> RubikaPreflightResult:
    """Evaluate whether a Rubika send may proceed for the EXACT account.

    Deterministic layers:
    A identity → B account state → C mode/config → D session readiness →
    E sender authorization → F lifecycle + rate policy → G Redis dependency safety.

    Phase 3: Layer F is the single authoritative Rubika send-policy path
    (lifecycle, daily/hourly caps, min interval, cooldown, throttle).
    """
    details: dict[str, Any] = {"context": context, "layer": "A"}

    # --- Layer A: Identity ---
    resolved_id = account.id if account is not None else account_id
    if account is None:
        if resolved_id is None:
            return _deny(code=ACCOUNT_MISSING, details=details)
        account = db.query(Account).filter(Account.id == int(resolved_id)).first()
    if account is None:
        return _deny(
            code=ACCOUNT_MISSING,
            account_id=_parse_int(resolved_id),
            details=details,
        )

    account_id = int(account.id)
    details["account_id"] = account_id

    if account.platform != PlatformType.RUBIKA:
        details["layer"] = "A"
        return _deny(
            code=WRONG_PLATFORM,
            account_id=account_id,
            details={**details, "platform": account.platform.value},
        )

    # --- Layer C early: delivery mode (needed for session_type labeling) ---
    details["layer"] = "C"
    try:
        mode = resolve_rubika_delivery_mode(rubika_delivery_mode=delivery_mode)
    except ValueError as exc:
        return _deny(
            code=CONFIG_INVALID,
            message=str(exc),
            account_id=account_id,
            details=details,
        )

    session_type = rubika_required_session_type(mode).value
    details["delivery_mode"] = mode

    # Defaults by mode / context
    side = context in {"side_channel", "status", "listener", "ai"}
    if check_runtime_limits is None:
        check_runtime_limits = mode == RUBIKA_MODE_USER_ACCOUNT and not side
    if check_pool_membership is None:
        check_pool_membership = mode == RUBIKA_MODE_USER_ACCOUNT and not side
    if check_send_window is None:
        check_send_window = mode == RUBIKA_MODE_USER_ACCOUNT and not side
    if check_campaign_assignment is None:
        check_campaign_assignment = not side

    if mode == RUBIKA_MODE_USER_ACCOUNT:
        from core_engine.services.rubika_mode import is_rubika_user_account_enabled

        enabled = (
            is_rubika_user_account_enabled()
            if user_account_enabled is None
            else bool(user_account_enabled)
        )
        if not enabled:
            return _deny(
                code=USER_ACCOUNT_DISABLED,
                account_id=account_id,
                delivery_mode=mode,
                session_type=session_type,
                details=details,
            )
    # bot_api has no separate "disabled" flag beyond env gates in delivery.py

    # --- Layer B: Account state (fail closed; ACTIVE only for send) ---
    details["layer"] = "B"
    if account.status == AccountStatus.BANNED:
        return _deny(
            code=ACCOUNT_BANNED,
            account_id=account_id,
            delivery_mode=mode,
            session_type=session_type,
            details=details,
        )
    if account.status == AccountStatus.REQUIRES_LOGIN:
        return _deny(
            code=ACCOUNT_REQUIRES_LOGIN,
            account_id=account_id,
            delivery_mode=mode,
            session_type=session_type,
            details=details,
        )
    if account.status == AccountStatus.RESTING:
        return _deny(
            code=ACCOUNT_DISABLED,
            account_id=account_id,
            delivery_mode=mode,
            session_type=session_type,
            details={**details, "lifecycle_state": "throttled"},
        )
    if account.status != AccountStatus.ACTIVE:
        return _deny(
            code=ACCOUNT_SUSPENDED,
            account_id=account_id,
            delivery_mode=mode,
            session_type=session_type,
            details={**details, "status": account.status.value},
        )

    # --- Layer D: Session readiness (Phase 1 engine) ---
    details["layer"] = "D"
    readiness = evaluate_account_session_readiness(
        db,
        account,
        rubika_delivery_mode=mode,
        rubika_user_account_enabled=user_account_enabled,
    )
    if not readiness.ready:
        code = readiness.code if readiness.code != READY else SESSION_INVALID
        return _deny(
            code=code,
            message=readiness.message or _PERSIAN_MESSAGES.get(code),
            account_id=account_id,
            delivery_mode=mode,
            session_type=readiness.session_type or session_type,
            details={**details, "readiness_error": readiness.error},
        )

    # --- Layer E: Sender authorization ---
    details["layer"] = "E"
    cid = _parse_int(campaign_id)
    if check_campaign_assignment and cid is not None:
        if not _campaign_allows_account(db, campaign_id=cid, account_id=account_id):
            return _deny(
                code=CAMPAIGN_ACCOUNT_NOT_ALLOWED,
                account_id=account_id,
                delivery_mode=mode,
                session_type=session_type,
                details={**details, "campaign_id": cid},
            )

    phase: str | None = None
    if check_send_window or check_pool_membership:
        from workers.rubika_account_pool import resolve_current_phase

        phase = resolve_current_phase(db)
        details["phase"] = phase
        if check_send_window and phase is None:
            return _deny(
                code=OUTSIDE_SEND_WINDOW,
                account_id=account_id,
                delivery_mode=mode,
                session_type=session_type,
                details=details,
            )

    if check_pool_membership and phase is not None:
        if not _account_in_phase_pool(db, account_id=account_id, phase=phase):
            return _deny(
                code=ACCOUNT_NOT_IN_ALLOWED_POOL,
                account_id=account_id,
                delivery_mode=mode,
                session_type=session_type,
                details=details,
            )

    # --- Layer F + G: Lifecycle + rate policy (Redis) ---
    if check_runtime_limits:
        details["layer"] = "F"
        if redis is None:
            try:
                from core_engine.services.redis_client import get_redis_client

                redis = get_redis_client()
            except Exception as exc:  # noqa: BLE001 — fail closed
                details["layer"] = "G"
                return _deny(
                    code=REDIS_UNAVAILABLE,
                    message=_PERSIAN_MESSAGES[REDIS_UNAVAILABLE],
                    account_id=account_id,
                    delivery_mode=mode,
                    session_type=session_type,
                    details={**details, "error": type(exc).__name__},
                )

        from core_engine.services.rubika_policy import (
            RubikaLifecycleState,
            build_policy_snapshot,
            compute_warmup_day,
            resolve_effective_limits,
            resolve_rubika_lifecycle,
        )
        from core_engine.services.rubika_quota import read_quota_snapshot

        try:
            from workers.config import get_worker_settings

            settings = get_worker_settings()
            cfg_hourly = int(
                hourly_cap
                if hourly_cap is not None
                else settings.RUBIKA_HOURLY_SEND_CAP
            )
            cfg_daily = int(
                daily_cap if daily_cap is not None else settings.RUBIKA_DAILY_SEND_CAP
            )
            cfg_min = int(settings.RUBIKA_MIN_SEND_DELAY_SECONDS)
            cfg_max = int(settings.RUBIKA_MAX_SEND_DELAY_SECONDS)
            jitter_enabled = bool(settings.RUBIKA_JITTER_ENABLED)

            snap = await read_quota_snapshot(redis, account_id, clock=clock)
            lifecycle = resolve_rubika_lifecycle(
                account,
                cooldown_active=bool(snap.cooldown_until),
                throttle_active=snap.throttle_active,
                clock=clock,
            )
            warmup_day = compute_warmup_day(
                getattr(account, "warming_started_at", None), clock=clock
            )
            limits = resolve_effective_limits(
                lifecycle,
                configured_daily_cap=cfg_daily,
                configured_hourly_cap=cfg_hourly,
                configured_min_interval=cfg_min,
                configured_max_interval=cfg_max,
                jitter_enabled=jitter_enabled,
            )

            policy_base = {
                "lifecycle_state": lifecycle.value,
                "warmup_day": warmup_day,
                "daily_cap": limits.daily_cap,
                "hourly_cap": limits.hourly_cap,
                "minimum_interval_seconds": limits.min_interval_seconds,
                "sent_today": snap.sent_today,
                "sent_this_hour": snap.sent_this_hour,
                "timezone": "Asia/Tehran",
                "send_window_phase": phase,
            }

            if lifecycle == RubikaLifecycleState.SUSPENDED:
                return _deny(
                    code=ACCOUNT_SUSPENDED,
                    account_id=account_id,
                    delivery_mode=mode,
                    session_type=session_type,
                    details={**details, **policy_base},
                )
            if lifecycle == RubikaLifecycleState.THROTTLED or snap.throttle_active:
                return _deny(
                    code=ACCOUNT_THROTTLED,
                    account_id=account_id,
                    delivery_mode=mode,
                    session_type=session_type,
                    details={**details, **policy_base},
                )
            if lifecycle == RubikaLifecycleState.COOLDOWN or snap.cooldown_until:
                return _deny(
                    code=COOLDOWN_ACTIVE,
                    account_id=account_id,
                    delivery_mode=mode,
                    session_type=session_type,
                    details={
                        **details,
                        **policy_base,
                        "cooldown_until": snap.cooldown_until,
                        "reason": snap.cooldown_reason,
                    },
                )

            if snap.delay_ttl_seconds > 0:
                from datetime import datetime, timedelta, timezone

                next_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=snap.delay_ttl_seconds)
                ).isoformat()
                if clock is not None:
                    base = clock if clock.tzinfo else clock.replace(tzinfo=timezone.utc)
                    next_at = (base.astimezone(timezone.utc) + timedelta(
                        seconds=snap.delay_ttl_seconds
                    )).isoformat()
                return _deny(
                    code=MIN_INTERVAL_ACTIVE,
                    account_id=account_id,
                    delivery_mode=mode,
                    session_type=session_type,
                    details={
                        **details,
                        **policy_base,
                        "retry_after_seconds": snap.delay_ttl_seconds,
                        "next_allowed_at": next_at,
                    },
                )

            if snap.sent_today >= limits.daily_cap:
                return _deny(
                    code=DAILY_CAP_REACHED,
                    account_id=account_id,
                    delivery_mode=mode,
                    session_type=session_type,
                    details={**details, **policy_base},
                )
            if snap.sent_this_hour >= limits.hourly_cap:
                return _deny(
                    code=HOURLY_CAP_REACHED,
                    account_id=account_id,
                    delivery_mode=mode,
                    session_type=session_type,
                    details={**details, **policy_base},
                )

            snapshot = build_policy_snapshot(
                account_id=account_id,
                lifecycle=lifecycle,
                warmup_day=warmup_day,
                limits=limits,
                sent_today=snap.sent_today,
                sent_this_hour=snap.sent_this_hour,
                allowed=True,
                code=READY,
                cooldown_until=snap.cooldown_until,
                cooldown_reason=snap.cooldown_reason,
                send_window_phase=phase,
                details={"context": context},
            )
            details["policy"] = {
                "lifecycle_state": snapshot.lifecycle_state,
                "warmup_day": snapshot.warmup_day,
                "sent_today": snapshot.sent_today,
                "daily_cap": snapshot.daily_cap,
                "remaining_daily": snapshot.remaining_daily,
                "sent_this_hour": snapshot.sent_this_hour,
                "hourly_cap": snapshot.hourly_cap,
                "remaining_hourly": snapshot.remaining_hourly,
                "minimum_interval_seconds": snapshot.minimum_interval_seconds,
                "timezone": snapshot.timezone,
                "send_window_phase": snapshot.send_window_phase,
            }
            # Suppress unused rng param lint — reserved for deterministic tests
            # that inject jitter at commit time in the connector.
            _ = rng
        except Exception as exc:  # noqa: BLE001 — Redis outage fail closed
            details["layer"] = "G"
            return _deny(
                code=REDIS_UNAVAILABLE,
                account_id=account_id,
                delivery_mode=mode,
                session_type=session_type,
                details={**details, "error": type(exc).__name__},
            )

    return _allow(
        account_id=account_id,
        delivery_mode=mode,
        session_type=session_type,
        details=details,
    )


async def require_rubika_side_channel_send(
    db: Session,
    *,
    account_id: int,
    context: str = "side_channel",
    redis: Any | None = None,
) -> RubikaPreflightResult:
    """Preflight for AI/listener/status transports.

    Phase 3: enforces lifecycle + rate policy (no campaign/pool/window).
    """
    return await evaluate_rubika_send_preflight(
        db,
        account_id=account_id,
        context=context,
        redis=redis,
        check_runtime_limits=True,
        check_pool_membership=False,
        check_send_window=False,
        check_campaign_assignment=False,
    )
