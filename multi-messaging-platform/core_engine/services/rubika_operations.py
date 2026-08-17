"""Rubika Phase 5 — protection operations overview aggregation.

Single-pass aggregation for the operator Protection Center.
No secret fields. Counts come from live Redis + DB evidence only.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    AuditLog,
    Campaign,
    CampaignAccount,
    CampaignStatus,
    Message,
    MessageAttempt,
    MessageAttemptStatus,
    PlatformType,
    RubikaAccountPool,
    SendStatus,
    CampaignRecipient,
)
from core_engine.services.account_session_wiring import evaluate_account_session_readiness
from core_engine.services.rubika_alerts import sync_alerts_from_protection
from core_engine.services.rubika_circuit import get_circuit_snapshot
from core_engine.services.rubika_health import build_health_snapshot
from core_engine.services.rubika_incidents import list_open_incidents
from core_engine.services.rubika_mode import resolve_rubika_delivery_mode
from core_engine.services.rubika_policy import (
    build_policy_snapshot,
    compute_warmup_day,
    resolve_effective_limits,
    resolve_rubika_lifecycle,
)
from core_engine.services.rubika_quota import read_quota_snapshot
from workers.config import get_worker_settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("core_engine.services.rubika_operations")

_PROTECTION_AUDIT_ACTIONS = (
    "rubika_account_restore",
    "rubika_pool_restore",
    "rubika_incident_acknowledge",
    "rubika_incident_resolve",
    "rubika_alert_acknowledge",
)

_CIRCUIT_ERROR_CODES = (
    "RUBIKA_CIRCUIT_OPEN",
    "rubika_circuit_open",
    "circuit_open",
)
_QUARANTINE_ERROR_CODES = (
    "ACCOUNT_QUARANTINED",
    "account_quarantined",
)


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _restore_eligibility(
    *,
    account: Account,
    quarantined: bool,
    session_ready: bool,
) -> dict[str, Any]:
    if account.platform != PlatformType.RUBIKA:
        return {
            "allowed": False,
            "code": "WRONG_PLATFORM",
            "reason": "اکانت روبیکا نیست.",
        }
    if account.status == AccountStatus.BANNED:
        return {
            "allowed": False,
            "code": "BANNED",
            "reason": "اکانت بن‌شده قابل بازگردانی نیست.",
        }
    if account.status == AccountStatus.REQUIRES_LOGIN:
        return {
            "allowed": False,
            "code": "REQUIRES_LOGIN",
            "reason": "اکانت نیاز به ورود مجدد دارد.",
        }
    if not quarantined:
        return {
            "allowed": False,
            "code": "NOT_QUARANTINED",
            "reason": "اکانت در قرنطینه نیست.",
        }
    if not session_ready:
        return {
            "allowed": False,
            "code": "SESSION_NOT_READY",
            "reason": "سشن آماده نیست؛ ابتدا ورود را تکمیل کنید.",
        }
    return {
        "allowed": True,
        "code": "RESTORE_AVAILABLE",
        "reason": "بازیابی دستی پس از تأیید اپراتور ممکن است.",
    }


def _preflight_display(
    *,
    circuit_state: str,
    quarantined: bool,
    account: Account,
    session_ready: bool,
    session_code: str | None,
    quota: Any,
    limits: Any,
) -> dict[str, Any]:
    """Canonical readiness vs send-now display codes (no TS safety logic)."""
    if circuit_state == "open":
        return {
            "send_allowed": False,
            "code": "RUBIKA_CIRCUIT_OPEN",
            "label": "Circuit باز",
        }
    if account.status == AccountStatus.BANNED:
        return {"send_allowed": False, "code": "ACCOUNT_BANNED", "label": "بن‌شده"}
    if account.status == AccountStatus.REQUIRES_LOGIN or (
        session_code == "ACCOUNT_REQUIRES_LOGIN"
    ):
        return {
            "send_allowed": False,
            "code": "REQUIRES_LOGIN",
            "label": "نیاز به ورود مجدد",
        }
    if quarantined:
        return {
            "send_allowed": False,
            "code": "ACCOUNT_QUARANTINED",
            "label": "قرنطینه",
        }
    if not session_ready:
        return {
            "send_allowed": False,
            "code": session_code or "SESSION_NOT_READY",
            "label": "سشن آماده نیست",
        }
    if getattr(quota, "cooldown_until", None):
        return {"send_allowed": False, "code": "COOLDOWN", "label": "Cooldown"}
    if getattr(quota, "throttle_active", False):
        return {
            "send_allowed": False,
            "code": "THROTTLED",
            "label": "محدودشده",
        }
    if getattr(quota, "delay_ttl_seconds", 0) > 0:
        return {
            "send_allowed": False,
            "code": "MIN_INTERVAL_ACTIVE",
            "label": "فاصله حداقل فعال",
        }
    if int(getattr(quota, "sent_today", 0)) >= int(limits.daily_cap):
        return {
            "send_allowed": False,
            "code": "DAILY_CAP_REACHED",
            "label": "سقف روزانه تکمیل",
        }
    if int(getattr(quota, "sent_this_hour", 0)) >= int(limits.hourly_cap):
        return {
            "send_allowed": False,
            "code": "HOURLY_CAP_REACHED",
            "label": "سقف ساعتی تکمیل",
        }
    if account.status != AccountStatus.ACTIVE:
        return {
            "send_allowed": False,
            "code": "ACCOUNT_DISABLED",
            "label": "غیرفعال",
        }
    return {"send_allowed": True, "code": "READY", "label": "آماده ارسال"}


def _campaign_impact(db: Session, *, quarantined_ids: list[int], circuit_open: bool) -> dict[str, Any]:
    """Evidence-backed impact only — never invent counts."""
    campaigns_quarantined = 0
    pending_blocked = 0
    retryable_blocked = 0
    circuit_waiting = 0
    running = (
        db.query(func.count(Campaign.id))
        .filter(
            Campaign.platform == PlatformType.RUBIKA,
            Campaign.status == CampaignStatus.RUNNING.value,
        )
        .scalar()
        or 0
    )
    paused_by_circuit = int(running) if circuit_open else 0
    pending_rubika = (
        db.query(func.count(CampaignRecipient.id))
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .filter(
            Campaign.platform == PlatformType.RUBIKA,
            CampaignRecipient.send_status.in_(
                (
                    SendStatus.PENDING,
                    SendStatus.QUEUED,
                    SendStatus.PROCESSING,
                    SendStatus.FAILED_RETRYABLE,
                )
            ),
        )
        .scalar()
        or 0
    )

    if quarantined_ids:
        campaigns_quarantined = (
            db.query(func.count(func.distinct(CampaignAccount.campaign_id)))
            .join(Campaign, Campaign.id == CampaignAccount.campaign_id)
            .filter(
                CampaignAccount.account_id.in_(quarantined_ids),
                CampaignAccount.enabled.is_(True),
                Campaign.status.in_(
                    (
                        CampaignStatus.RUNNING.value,
                        CampaignStatus.PREPARED.value,
                        CampaignStatus.PAUSED.value,
                    )
                ),
            )
            .scalar()
            or 0
        )
        pending_blocked = (
            db.query(func.count(Message.id))
            .join(CampaignRecipient, CampaignRecipient.final_message_id == Message.id)
            .filter(
                Message.account_id.in_(quarantined_ids),
                CampaignRecipient.send_status.in_(
                    (
                        SendStatus.PENDING,
                        SendStatus.QUEUED,
                        SendStatus.PROCESSING,
                        SendStatus.FAILED_RETRYABLE,
                    )
                ),
            )
            .scalar()
            or 0
        )
        # Fallback: messages without recipient join still pending via attempts
        retryable_blocked = (
            db.query(func.count(MessageAttempt.id))
            .join(Message, Message.id == MessageAttempt.message_id)
            .filter(
                Message.account_id.in_(quarantined_ids),
                MessageAttempt.status == MessageAttemptStatus.FAILED_RETRYABLE,
                MessageAttempt.error_code.in_(_QUARANTINE_ERROR_CODES),
            )
            .scalar()
            or 0
        )

    if circuit_open:
        circuit_waiting = (
            db.query(func.count(MessageAttempt.id))
            .join(Message, Message.id == MessageAttempt.message_id)
            .join(Account, Account.id == Message.account_id)
            .filter(
                Account.platform == PlatformType.RUBIKA,
                MessageAttempt.error_code.in_(_CIRCUIT_ERROR_CODES),
            )
            .scalar()
            or 0
        )

    return {
        "campaigns_affected_by_quarantine": int(campaigns_quarantined),
        "pending_messages_blocked": int(pending_blocked),
        "retryable_blocked_messages": int(retryable_blocked),
        "messages_waiting_circuit_open": int(circuit_waiting),
        "running_campaigns": int(running),
        "campaigns_paused_by_circuit": int(paused_by_circuit),
        "pending_rubika_messages": int(pending_rubika),
    }


def _protection_events(db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.action.in_(_PROTECTION_AUDIT_ACTIONS))
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        details = row.details if isinstance(row.details, dict) else {}
        # Never surface secret-looking keys
        safe_details = {
            k: v
            for k, v in details.items()
            if not any(
                tok in str(k).lower()
                for tok in ("token", "password", "otp", "secret", "ciphertext", "auth")
            )
        }
        events.append(
            {
                "time": _iso_or_none(row.timestamp),
                "scope": "ACCOUNT" if row.resource_type == "account" else "SYSTEM",
                "account_id": int(row.resource_id)
                if row.resource_type == "account" and str(row.resource_id).isdigit()
                else None,
                "event": row.action,
                "reason": safe_details.get("reason")
                or safe_details.get("code")
                or row.action,
                "username": row.username,
                "result": safe_details.get("result"),
                "source": "audit_log",
            }
        )
    return events


async def build_protection_overview(
    db: Session,
    redis: "Redis",
    *,
    clock: datetime | None = None,
    include_alerts_sync: bool = True,
) -> dict[str, Any]:
    settings = get_worker_settings()
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    redis_ok = True
    circuit = None
    incidents: list = []
    try:
        await redis.ping()
        circuit = await get_circuit_snapshot(
            redis,
            probe_budget=int(settings.RUBIKA_CIRCUIT_PROBE_BUDGET),
            window_seconds=int(settings.RUBIKA_CIRCUIT_WINDOW_SECONDS),
            clock=now,
        )
        incidents = await list_open_incidents(redis)
    except Exception as exc:  # noqa: BLE001
        redis_ok = False
        logger.warning("event=rubika_protection_redis_unavailable error=%s", exc)
        from core_engine.services.rubika_circuit import RubikaCircuitSnapshot

        circuit = RubikaCircuitSnapshot(
            state="open",
            opened_at=None,
            half_open_at=None,
            open_until=None,
            probe_budget=0,
            probe_remaining=0,
            systemic_account_count=0,
            systemic_failure_count=0,
            reason="redis_unavailable",
        )
        incidents = []

    incident_dicts = [i.to_dict() for i in incidents]
    last_incident_by_account: dict[int, dict[str, Any]] = {}
    for item in incident_dicts:
        aid = item.get("account_id")
        if aid is None:
            continue
        aid_i = int(aid)
        prev = last_incident_by_account.get(aid_i)
        if prev is None or str(item.get("last_seen_at") or "") > str(
            prev.get("last_seen_at") or ""
        ):
            last_incident_by_account[aid_i] = item

    rows = (
        db.query(Account, RubikaAccountPool)
        .outerjoin(RubikaAccountPool, RubikaAccountPool.account_id == Account.id)
        .filter(Account.platform == PlatformType.RUBIKA)
        .order_by(Account.id.asc())
        .all()
    )

    accounts_out: list[dict[str, Any]] = []
    summary = {
        "total_accounts": 0,
        "ready": 0,
        "healthy": 0,
        "degraded": 0,
        "throttled": 0,
        "quarantined": 0,
        "requires_login": 0,
        "critical": 0,
        "offline": 0,
        "open_incidents": len(incident_dicts),
        "critical_incidents": sum(
            1 for i in incident_dicts if str(i.get("severity") or "").upper() == "CRITICAL"
        ),
        "circuit_state": circuit.state,
        "unresolved_alerts": 0,
        "critical_alerts": 0,
    }

    quarantined_ids: list[int] = []

    for account, pool in rows:
        summary["total_accounts"] += 1
        readiness = evaluate_account_session_readiness(db, account)
        if redis_ok:
            health = await build_health_snapshot(
                redis,
                account,
                session_ready=readiness.ready,
                clock=now,
            )
            quota = await read_quota_snapshot(redis, account.id, clock=now)
        else:
            from core_engine.services.rubika_health import RubikaHealthSnapshot, RubikaHealthState
            from core_engine.services.rubika_quota import RubikaQuotaSnapshot

            lifecycle_fb = resolve_rubika_lifecycle(
                account, cooldown_active=False, throttle_active=False, clock=now
            )
            health = RubikaHealthSnapshot(
                account_id=account.id,
                health_state=(
                    RubikaHealthState.OFFLINE.value
                    if account.status == AccountStatus.BANNED
                    else RubikaHealthState.CRITICAL.value
                    if account.status == AccountStatus.REQUIRES_LOGIN
                    else RubikaHealthState.CRITICAL.value
                ),
                lifecycle_state=lifecycle_fb.value,
                session_ready=readiness.ready,
                successes_window=0,
                failures_window=0,
                failure_rate=0.0,
                consecutive_failures=0,
                last_success_at=None,
                last_failure_at=None,
                last_failure_code=None,
                last_failure_category=None,
                cooldown_until=None,
                throttle_state=False,
                quarantined=False,
                quarantine_reason=None,
                evaluated_at=now.isoformat(),
                details={"redis_ok": False},
            )
            quota = RubikaQuotaSnapshot(
                sent_today=0,
                sent_this_hour=0,
                day_bucket="",
                hour_bucket="",
                delay_ttl_seconds=0,
                cooldown_until=None,
                cooldown_reason=None,
                throttle_active=False,
            )
        lifecycle = resolve_rubika_lifecycle(
            account,
            cooldown_active=bool(quota.cooldown_until),
            throttle_active=bool(quota.throttle_active),
            clock=now,
        )
        limits = resolve_effective_limits(
            lifecycle,
            configured_daily_cap=int(settings.RUBIKA_DAILY_SEND_CAP),
            configured_hourly_cap=int(settings.RUBIKA_HOURLY_SEND_CAP),
            configured_min_interval=int(settings.RUBIKA_MIN_SEND_DELAY_SECONDS),
            configured_max_interval=max(
                int(settings.RUBIKA_MIN_SEND_DELAY_SECONDS),
                int(getattr(settings, "RUBIKA_MAX_SEND_DELAY_SECONDS", 30) or 30),
            ),
            jitter_enabled=bool(settings.RUBIKA_JITTER_ENABLED),
        )
        next_allowed = None
        if quota.delay_ttl_seconds > 0:
            next_allowed = (now + timedelta(seconds=quota.delay_ttl_seconds)).isoformat()
        policy = build_policy_snapshot(
            account_id=account.id,
            lifecycle=lifecycle,
            warmup_day=compute_warmup_day(
                getattr(account, "warming_started_at", None), clock=now
            ),
            limits=limits,
            sent_today=quota.sent_today,
            sent_this_hour=quota.sent_this_hour,
            allowed=False,
            code="OVERVIEW",
            next_allowed_send_at=next_allowed,
            cooldown_until=quota.cooldown_until,
            cooldown_reason=quota.cooldown_reason,
            last_send_at=_iso_or_none(account.last_used_at),
        )
        preflight = _preflight_display(
            circuit_state=circuit.state,
            quarantined=health.quarantined,
            account=account,
            session_ready=readiness.ready,
            session_code=readiness.code,
            quota=quota,
            limits=limits,
        )
        if preflight["send_allowed"]:
            summary["ready"] += 1

        hs = health.health_state
        if hs == "healthy":
            summary["healthy"] += 1
        elif hs == "degraded":
            summary["degraded"] += 1
        elif hs == "throttled":
            summary["throttled"] += 1
        elif hs == "quarantined":
            summary["quarantined"] += 1
            quarantined_ids.append(account.id)
        elif hs == "critical":
            summary["critical"] += 1
        elif hs == "offline":
            summary["offline"] += 1

        if account.status == AccountStatus.REQUIRES_LOGIN:
            summary["requires_login"] += 1

        last_inc = last_incident_by_account.get(account.id)
        restore = _restore_eligibility(
            account=account,
            quarantined=health.quarantined,
            session_ready=readiness.ready,
        )
        try:
            delivery_mode = resolve_rubika_delivery_mode()
        except Exception:  # noqa: BLE001
            delivery_mode = None

        accounts_out.append(
            {
                "account_id": account.id,
                "label": account.label,
                "phone_number": account.phone_number,
                "delivery_mode": delivery_mode,
                "account_status": account.status.value,
                "session_ready": readiness.ready,
                "session_code": readiness.code,
                "session_message": readiness.message,
                "readiness": {
                    "ready": readiness.ready,
                    "code": readiness.code,
                    "message": readiness.message,
                },
                "preflight": preflight,
                "lifecycle_state": health.lifecycle_state,
                "health_state": health.health_state,
                "pool_phase": pool.phase if pool else "unassigned",
                "priority": pool.priority if pool else 0,
                "sent_today": policy.sent_today,
                "daily_cap": policy.daily_cap,
                "remaining_daily": policy.remaining_daily,
                "sent_this_hour": policy.sent_this_hour,
                "hourly_cap": policy.hourly_cap,
                "remaining_hourly": policy.remaining_hourly,
                "minimum_interval_seconds": policy.minimum_interval_seconds,
                "next_allowed_send_at": policy.next_allowed_send_at,
                "cooldown_until": policy.cooldown_until,
                "last_successful_send_at": health.last_success_at,
                "last_failure_at": health.last_failure_at,
                "last_failure_code": health.last_failure_code,
                "failure_rate": health.failure_rate,
                "failures_window": health.failures_window,
                "successes_window": health.successes_window,
                "consecutive_failures": health.consecutive_failures,
                "quarantined": health.quarantined,
                "quarantine_reason": health.quarantine_reason,
                "last_incident": last_inc,
                "restore": restore,
                "health": health.to_dict(),
                "policy": asdict(policy),
            }
        )

    impact = _campaign_impact(
        db, quarantined_ids=quarantined_ids, circuit_open=circuit.state == "open"
    )

    alerts: list[dict[str, Any]] = []
    if include_alerts_sync and redis_ok:
        alert_objs = await sync_alerts_from_protection(
            redis,
            circuit_state=circuit.state,
            circuit_reason=circuit.reason,
            accounts=accounts_out,
            incidents=incident_dicts,
            redis_ok=True,
            clock=now,
        )
        alerts = [a.to_dict() for a in alert_objs]
    summary["unresolved_alerts"] = len(alerts)
    summary["critical_alerts"] = sum(
        1 for a in alerts if str(a.get("severity") or "").upper() == "CRITICAL"
    )

    # Merge incident-derived timeline events (no secrets)
    timeline = _protection_events(db, limit=40)
    for inc in incident_dicts[:20]:
        timeline.append(
            {
                "time": inc.get("last_seen_at") or inc.get("opened_at"),
                "scope": inc.get("scope"),
                "account_id": inc.get("account_id"),
                "event": "incident_opened"
                if inc.get("status") == "OPEN"
                else "incident_resolved",
                "reason": inc.get("reason") or inc.get("code"),
                "username": None,
                "result": inc.get("status"),
                "source": "incident",
            }
        )
    timeline.sort(key=lambda e: str(e.get("time") or ""), reverse=True)
    timeline = timeline[:50]

    return {
        "evaluated_at": now.isoformat(),
        "redis_ok": redis_ok,
        "system": {
            "circuit": {
                "state": circuit.state,
                "opened_at": circuit.opened_at,
                "half_open_at": circuit.half_open_at,
                "open_until": circuit.open_until,
                "probe_budget": circuit.probe_budget,
                "probe_remaining": circuit.probe_remaining,
                "systemic_account_count": circuit.systemic_account_count,
                "systemic_failure_count": circuit.systemic_failure_count,
                "reason": circuit.reason,
            },
            "open_incidents": len(incident_dicts),
            "redis_ok": redis_ok,
        },
        "summary": summary,
        "accounts": accounts_out,
        "incidents": incident_dicts,
        "alerts": alerts,
        "campaign_impact": impact,
        "events": timeline,
        "operator_notes": {
            "circuit_force_close": False,
            "circuit_force_close_reason": (
                "Force-close is not exposed: closing without half-open probe evidence "
                "would weaken Phase 4 fail-closed safety. Refresh and controlled "
                "HALF_OPEN evaluation remain the supported path."
            ),
        },
    }


async def build_account_protection_detail(
    db: Session,
    redis: "Redis",
    account_id: int,
    *,
    clock: datetime | None = None,
) -> dict[str, Any] | None:
    overview = await build_protection_overview(
        db, redis, clock=clock, include_alerts_sync=False
    )
    account_row = next(
        (a for a in overview["accounts"] if a["account_id"] == account_id), None
    )
    if account_row is None:
        return None

    open_incidents = [
        i for i in overview["incidents"] if i.get("account_id") == account_id
    ]
    events = [e for e in overview["events"] if e.get("account_id") == account_id]

    # Campaign usage for this sender (assigned semantics preserved)
    campaign_rows = (
        db.query(Campaign.id, Campaign.title, Campaign.status, CampaignAccount.enabled)
        .join(CampaignAccount, CampaignAccount.campaign_id == Campaign.id)
        .filter(CampaignAccount.account_id == account_id)
        .order_by(Campaign.id.desc())
        .limit(30)
        .all()
    )
    campaigns = [
        {
            "campaign_id": row.id,
            "title": row.title,
            "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "enabled": bool(row.enabled),
        }
        for row in campaign_rows
    ]

    pending = (
        db.query(func.count(Message.id))
        .join(CampaignRecipient, CampaignRecipient.final_message_id == Message.id)
        .filter(
            Message.account_id == account_id,
            CampaignRecipient.send_status.in_(
                (
                    SendStatus.PENDING,
                    SendStatus.QUEUED,
                    SendStatus.PROCESSING,
                    SendStatus.FAILED_RETRYABLE,
                )
            ),
        )
        .scalar()
        or 0
    )

    recent_failures = []
    if account_row.get("last_failure_at") or account_row.get("last_failure_code"):
        recent_failures.append(
            {
                "at": account_row.get("last_failure_at"),
                "code": account_row.get("last_failure_code"),
                "category": (account_row.get("health") or {}).get("last_failure_category"),
            }
        )

    return {
        "account": account_row,
        "identity": {
            "account_id": account_id,
            "label": account_row.get("label"),
            "phone_number": account_row.get("phone_number"),
            "delivery_mode": account_row.get("delivery_mode"),
            "account_status": account_row.get("account_status"),
        },
        "session": account_row.get("readiness"),
        "lifecycle": {
            "lifecycle_state": account_row.get("lifecycle_state"),
            "policy": account_row.get("policy"),
        },
        "quota": {
            "sent_today": account_row.get("sent_today"),
            "daily_cap": account_row.get("daily_cap"),
            "remaining_daily": account_row.get("remaining_daily"),
            "sent_this_hour": account_row.get("sent_this_hour"),
            "hourly_cap": account_row.get("hourly_cap"),
            "remaining_hourly": account_row.get("remaining_hourly"),
            "minimum_interval_seconds": account_row.get("minimum_interval_seconds"),
            "next_allowed_send_at": account_row.get("next_allowed_send_at"),
            "cooldown_until": account_row.get("cooldown_until"),
        },
        "health": account_row.get("health"),
        "recent_failures": recent_failures,
        "open_incidents": open_incidents,
        "protection_events": events[:20],
        "campaign_usage": {
            "campaigns": campaigns,
            "pending_messages": int(pending),
        },
        "actions": {
            "restore": account_row.get("restore"),
        },
        "system": overview["system"],
    }
