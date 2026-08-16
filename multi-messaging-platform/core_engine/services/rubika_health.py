"""Rubika Phase 4 — account health, quarantine, success/failure recording.

Lifecycle (Phase 3) = sending maturity / rate policy.
Health (Phase 4) = operational condition.

Quarantine persistence (no migration):
- Redis key ``rubika:quarantine:{id}`` without TTL for durable/manual-review
- Also sets AccountStatus.RESTING so restart without Redis still blocks sends
- Transient quarantine may use TTL when ``ttl_seconds > 0``

Dependency / policy failures do NOT escalate account guilt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from core_engine.models import Account, AccountStatus, PlatformType
from core_engine.services.rubika_circuit import (
    record_probe_failure,
    record_probe_success,
    record_systemic_failure,
)
from core_engine.services.rubika_failure import (
    RubikaFailureCategory,
    RubikaFailureEvent,
    RubikaFailureSeverity,
    classify_rubika_failure,
    is_account_guilt_category,
    is_session_auth_category,
)
from core_engine.services.rubika_incidents import open_or_update_incident, resolve_incident
from core_engine.services.rubika_policy import resolve_rubika_lifecycle
from core_engine.services.rubika_quota import (
    clear_failure_count,
    enter_cooldown,
    enter_throttle,
)
from workers.redis_keys import (
    rubika_health_consec_key,
    rubika_health_fail_key,
    rubika_health_meta_key,
    rubika_health_success_key,
    rubika_quarantine_key,
    rubika_throttle_key,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("core_engine.services.rubika_health")


class RubikaHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    THROTTLED = "throttled"
    QUARANTINED = "quarantined"
    CRITICAL = "critical"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class RubikaHealthSnapshot:
    account_id: int
    health_state: str
    lifecycle_state: str
    session_ready: bool | None
    successes_window: int
    failures_window: int
    failure_rate: float
    consecutive_failures: int
    last_success_at: str | None
    last_failure_at: str | None
    last_failure_code: str | None
    last_failure_category: str | None
    cooldown_until: str | None
    throttle_state: bool
    quarantined: bool
    quarantine_reason: str | None
    evaluated_at: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass(frozen=True, slots=True)
class RubikaRestoreResult:
    ok: bool
    code: str
    message: str
    account_id: int | None = None
    health_state: str | None = None


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
    return str(int(now.timestamp()) // max(1, window_seconds))


async def get_quarantine_meta(redis: "Redis", account_id: int) -> dict[str, Any] | None:
    raw = await redis.get(rubika_quarantine_key(account_id))
    if not raw:
        return None
    try:
        return json.loads(_decode(raw))
    except json.JSONDecodeError:
        return {"reason": "quarantined", "manual_review_required": True}


async def is_account_quarantined(redis: "Redis", account_id: int) -> bool:
    return bool(await redis.exists(rubika_quarantine_key(account_id)))


async def quarantine_account(
    redis: "Redis",
    db: Session | None,
    account_id: int,
    *,
    reason: str,
    source: str,
    last_failure_code: str | None = None,
    manual_review_required: bool = True,
    ttl_seconds: int = 0,
    clock: datetime | None = None,
) -> dict[str, Any]:
    now = _now(clock)
    meta = {
        "account_id": int(account_id),
        "reason": reason,
        "source": source,
        "started_at": _iso(now),
        "until": None
        if ttl_seconds <= 0
        else datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc).isoformat(),
        "manual_review_required": bool(manual_review_required),
        "last_failure_code": last_failure_code,
    }
    key = rubika_quarantine_key(account_id)
    if ttl_seconds > 0:
        await redis.set(key, json.dumps(meta), ex=int(ttl_seconds))
    else:
        await redis.set(key, json.dumps(meta))

    if db is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        if account is not None and account.status == AccountStatus.ACTIVE:
            account.status = AccountStatus.RESTING
            db.flush()

    await open_or_update_incident(
        redis,
        scope="ACCOUNT",
        category="quarantine",
        severity=RubikaFailureSeverity.CRITICAL.value,
        reason=reason,
        source=source,
        account_id=account_id,
        code=last_failure_code or "ACCOUNT_QUARANTINED",
        clock=clock,
    )
    logger.warning(
        "event=rubika_account_quarantined account_id=%s reason=%s source=%s",
        account_id,
        reason,
        source,
    )
    return meta


async def clear_quarantine(redis: "Redis", account_id: int) -> None:
    await redis.delete(rubika_quarantine_key(account_id))
    await resolve_incident(
        redis,
        scope="ACCOUNT",
        category="quarantine",
        code="ACCOUNT_QUARANTINED",
        account_id=account_id,
    )


async def build_health_snapshot(
    redis: "Redis",
    account: Account,
    *,
    window_seconds: int = 3600,
    session_ready: bool | None = None,
    clock: datetime | None = None,
) -> RubikaHealthSnapshot:
    now = _now(clock)
    bucket = _window_bucket(now, window_seconds)
    ok = int(await redis.get(rubika_health_success_key(account.id, bucket)) or 0)
    fail = int(await redis.get(rubika_health_fail_key(account.id, bucket)) or 0)
    consec = int(await redis.get(rubika_health_consec_key(account.id)) or 0)
    meta_raw = await redis.get(rubika_health_meta_key(account.id))
    meta: dict[str, Any] = {}
    if meta_raw:
        try:
            meta = json.loads(_decode(meta_raw))
        except json.JSONDecodeError:
            meta = {}
    q = await get_quarantine_meta(redis, account.id)
    throttle = bool(await redis.exists(rubika_throttle_key(account.id)))
    total = ok + fail
    rate = (fail / total) if total else 0.0

    if account.status == AccountStatus.BANNED:
        health = RubikaHealthState.OFFLINE
    elif account.status == AccountStatus.REQUIRES_LOGIN:
        health = RubikaHealthState.CRITICAL
    elif q is not None:
        health = RubikaHealthState.QUARANTINED
    elif throttle or account.status == AccountStatus.RESTING:
        health = RubikaHealthState.THROTTLED
    elif consec >= 3 or (total >= 5 and rate >= 0.5):
        health = RubikaHealthState.DEGRADED
    else:
        health = RubikaHealthState.HEALTHY

    lifecycle = resolve_rubika_lifecycle(
        account,
        cooldown_active=False,
        throttle_active=throttle,
        clock=clock,
    )
    return RubikaHealthSnapshot(
        account_id=int(account.id),
        health_state=health.value,
        lifecycle_state=lifecycle.value,
        session_ready=session_ready,
        successes_window=ok,
        failures_window=fail,
        failure_rate=round(rate, 4),
        consecutive_failures=consec,
        last_success_at=meta.get("last_success_at"),
        last_failure_at=meta.get("last_failure_at"),
        last_failure_code=meta.get("last_failure_code"),
        last_failure_category=meta.get("last_failure_category"),
        cooldown_until=None,
        throttle_state=throttle,
        quarantined=q is not None,
        quarantine_reason=(q or {}).get("reason"),
        evaluated_at=_iso(now),
        details={"window_seconds": window_seconds},
    )


async def record_rubika_send_success(
    redis: "Redis",
    account_id: int,
    *,
    window_seconds: int = 3600,
    probe_token: str | None = None,
    half_open_successes_to_close: int = 1,
    clock: datetime | None = None,
) -> None:
    """Health evidence only — does not touch Phase 3 quota counters."""
    now = _now(clock)
    bucket = _window_bucket(now, window_seconds)
    key = rubika_health_success_key(account_id, bucket)
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, window_seconds * 2)
    await redis.delete(rubika_health_consec_key(account_id))
    await clear_failure_count(redis, account_id)

    meta_raw = await redis.get(rubika_health_meta_key(account_id))
    meta: dict[str, Any] = {}
    if meta_raw:
        try:
            meta = json.loads(_decode(meta_raw))
        except json.JSONDecodeError:
            meta = {}
    meta["last_success_at"] = _iso(now)
    await redis.set(rubika_health_meta_key(account_id), json.dumps(meta), ex=86400)

    # Circuit HALF_OPEN probe success
    from core_engine.services.rubika_circuit import get_circuit_state, RubikaCircuitState

    if await get_circuit_state(redis) == RubikaCircuitState.HALF_OPEN:
        await record_probe_success(
            redis,
            successes_to_close=half_open_successes_to_close,
            clock=clock,
        )


async def record_rubika_send_failure(
    redis: "Redis",
    db: Session | None,
    *,
    account_id: int | None,
    code: str | None,
    retryable: bool = True,
    mode: str | None = None,
    campaign_id: int | str | None = None,
    message_id: int | str | None = None,
    source: str = "connector",
    details: dict[str, Any] | None = None,
    window_seconds: int = 3600,
    consecutive_threshold: int = 5,
    failure_count_threshold: int = 10,
    failure_ratio_threshold: float = 0.5,
    min_samples: int = 5,
    cooldown_threshold: int = 3,
    cooldown_seconds: int = 300,
    throttle_seconds: int = 600,
    circuit_distinct_accounts: int = 3,
    circuit_failure_threshold: int = 15,
    circuit_window_seconds: int = 300,
    circuit_open_seconds: int = 120,
    clock: datetime | None = None,
) -> RubikaFailureEvent:
    event = classify_rubika_failure(
        code,
        retryable=retryable,
        account_id=account_id,
        campaign_id=campaign_id,
        message_id=message_id,
        mode=mode,
        source=source,
        details=details,
        clock=clock,
    )

    # Dependency → system incident, never account quarantine
    if event.category in (
        RubikaFailureCategory.DEPENDENCY_REDIS,
        RubikaFailureCategory.DEPENDENCY_DB,
    ):
        await open_or_update_incident(
            redis,
            scope="SYSTEM",
            category=event.category.value,
            severity=event.severity.value,
            reason=f"dependency:{event.code}",
            source=source,
            account_id=None,
            code=event.code,
            clock=clock,
        )
        await record_systemic_failure(
            redis,
            account_id=None,
            distinct_accounts_threshold=circuit_distinct_accounts,
            failure_count_threshold=max(1, circuit_failure_threshold // 3),
            window_seconds=circuit_window_seconds,
            open_seconds=circuit_open_seconds,
            clock=clock,
            reason=f"dependency:{event.category.value}",
        )
        return event

    if event.category == RubikaFailureCategory.POLICY:
        return event  # policy denials are not health guilt

    if account_id is None:
        return event

    now = _now(clock)
    bucket = _window_bucket(now, window_seconds)
    fail_key = rubika_health_fail_key(account_id, bucket)
    fail_count = int(await redis.incr(fail_key))
    if fail_count == 1:
        await redis.expire(fail_key, window_seconds * 2)
    consec = int(await redis.incr(rubika_health_consec_key(account_id)))
    await redis.expire(rubika_health_consec_key(account_id), window_seconds * 2)

    meta = {
        "last_failure_at": _iso(now),
        "last_failure_code": event.code,
        "last_failure_category": event.category.value,
    }
    prev = await redis.get(rubika_health_meta_key(account_id))
    if prev:
        try:
            merged = json.loads(_decode(prev))
            merged.update(meta)
            meta = merged
        except json.JSONDecodeError:
            pass
    await redis.set(rubika_health_meta_key(account_id), json.dumps(meta), ex=86400)

    # Phase 3 ladder for transport/rate (account guilt only)
    if is_account_guilt_category(event.category):
        action = None
        if event.category in (
            RubikaFailureCategory.TRANSPORT,
            RubikaFailureCategory.TIMEOUT,
            RubikaFailureCategory.RATE_LIMIT,
            RubikaFailureCategory.UNKNOWN,
        ):
            from core_engine.services.rubika_quota import record_send_failure

            action = await record_send_failure(
                redis,
                account_id,
                threshold=cooldown_threshold,
                cooldown_seconds=cooldown_seconds,
                throttle_seconds=throttle_seconds,
                clock=clock,
            )
            if action == "cooldown":
                logger.warning(
                    "event=rubika_account_degraded account_id=%s via=cooldown code=%s",
                    account_id,
                    event.code,
                )
            elif action == "throttle":
                logger.warning(
                    "event=rubika_account_throttled account_id=%s code=%s",
                    account_id,
                    event.code,
                )

        ok = int(await redis.get(rubika_health_success_key(account_id, bucket)) or 0)
        total = ok + fail_count
        ratio = fail_count / total if total else 0.0

        # Escalation
        if is_session_auth_category(event.category) and consec >= 2:
            await quarantine_account(
                redis,
                db,
                account_id,
                reason="repeated_session_auth_failure",
                source=source,
                last_failure_code=event.code,
                manual_review_required=True,
                ttl_seconds=0,
                clock=clock,
            )
            if db is not None:
                account = db.query(Account).filter(Account.id == account_id).first()
                if account is not None and account.status not in (
                    AccountStatus.BANNED,
                    AccountStatus.REQUIRES_LOGIN,
                ):
                    account.status = AccountStatus.REQUIRES_LOGIN
                    db.flush()
        elif consec >= consecutive_threshold or (
            total >= min_samples and ratio >= failure_ratio_threshold
        ) or fail_count >= failure_count_threshold:
            if consec >= consecutive_threshold * 2:
                await quarantine_account(
                    redis,
                    db,
                    account_id,
                    reason="sustained_failure_rate",
                    source=source,
                    last_failure_code=event.code,
                    manual_review_required=True,
                    ttl_seconds=3600,
                    clock=clock,
                )
            else:
                await enter_throttle(
                    redis,
                    account_id,
                    seconds=throttle_seconds,
                    reason="elevated_failure_rate",
                )
                logger.warning(
                    "event=rubika_account_throttled account_id=%s consec=%s ratio=%s",
                    account_id,
                    consec,
                    ratio,
                )

        # Systemic circuit signal (multi-account)
        from core_engine.services.rubika_circuit import get_circuit_state, RubikaCircuitState

        state = await get_circuit_state(redis)
        if state == RubikaCircuitState.HALF_OPEN:
            await record_probe_failure(
                redis, open_seconds=circuit_open_seconds, clock=clock
            )
        else:
            await record_systemic_failure(
                redis,
                account_id=account_id,
                distinct_accounts_threshold=circuit_distinct_accounts,
                failure_count_threshold=circuit_failure_threshold,
                window_seconds=circuit_window_seconds,
                open_seconds=circuit_open_seconds,
                clock=clock,
            )

        await open_or_update_incident(
            redis,
            scope="ACCOUNT",
            category=event.category.value,
            severity=event.severity.value,
            reason=event.code,
            source=source,
            account_id=account_id,
            code=event.code,
            clock=clock,
        )

    return event


async def restore_rubika_account(
    db: Session,
    redis: "Redis",
    *,
    account_id: int,
    username: str = "system",
    require_session_ready: bool = True,
    clock: datetime | None = None,
) -> RubikaRestoreResult:
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        return RubikaRestoreResult(False, "ACCOUNT_NOT_FOUND", "اکانت پیدا نشد.")
    if account.platform != PlatformType.RUBIKA:
        return RubikaRestoreResult(
            False, "WRONG_PLATFORM", "اکانت روبیکا نیست.", account_id=account_id
        )
    if account.status == AccountStatus.BANNED:
        return RubikaRestoreResult(
            False,
            "BANNED",
            "اکانت بن‌شده قابل بازگردانی خودکار نیست.",
            account_id=account_id,
        )

    quarantined = await is_account_quarantined(redis, account_id)
    if not quarantined and account.status == AccountStatus.ACTIVE:
        return RubikaRestoreResult(
            False,
            "NOT_QUARANTINED",
            "اکانت در قرنطینه نیست.",
            account_id=account_id,
            health_state=RubikaHealthState.HEALTHY.value,
        )

    if require_session_ready:
        from core_engine.models import ChannelSession
        from core_engine.services.account_session_wiring import (
            evaluate_account_session_readiness,
        )
        from core_engine.services.session_storage import load_channel_session_plaintext

        # Quarantine sets RESTING; readiness engine rejects RESTING as disabled.
        # For restore we verify encrypted session material while still RESTING.
        if account.status == AccountStatus.RESTING and quarantined:
            row = (
                db.query(ChannelSession)
                .filter(ChannelSession.account_id == account_id)
                .order_by(ChannelSession.id.desc())
                .first()
            )
            if row is None:
                return RubikaRestoreResult(
                    False,
                    "SESSION_NOT_READY",
                    "سشن ثبت نشده است.",
                    account_id=account_id,
                )
            try:
                load_channel_session_plaintext(row)
            except Exception as exc:  # noqa: BLE001
                return RubikaRestoreResult(
                    False,
                    "SESSION_NOT_READY",
                    str(exc) or "سشن آماده نیست.",
                    account_id=account_id,
                )
        else:
            readiness = evaluate_account_session_readiness(db, account)
            if not readiness.ready:
                return RubikaRestoreResult(
                    False,
                    "SESSION_NOT_READY",
                    readiness.message or "سشن آماده نیست.",
                    account_id=account_id,
                )

    await clear_quarantine(redis, account_id)
    await redis.delete(rubika_throttle_key(account_id))
    await redis.delete(rubika_health_consec_key(account_id))
    await clear_failure_count(redis, account_id)

    if account.status in (AccountStatus.RESTING, AccountStatus.REQUIRES_LOGIN):
        # REQUIRES_LOGIN only cleared if session ready (already checked)
        account.status = AccountStatus.ACTIVE
        db.flush()

    from core_engine.services.audit_service import record_audit

    record_audit(
        db,
        username=username,
        action="rubika_account_restore",
        resource_type="account",
        resource_id=str(account_id),
        details={"previous_quarantined": quarantined},
    )
    db.commit()
    logger.info(
        "event=rubika_account_recovered account_id=%s by=%s",
        account_id,
        username,
    )
    snap = await build_health_snapshot(redis, account, clock=clock)
    return RubikaRestoreResult(
        True,
        "RESTORED",
        "اکانت با موفقیت بازگردانی شد.",
        account_id=account_id,
        health_state=snap.health_state,
    )
