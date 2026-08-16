"""Rubika Phase 5 — operator alert model with Redis dedupe.

Alerts are derived from protection evidence (health / circuit / incidents).
No secrets may appear in title, message, or metadata.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from workers.redis_keys import rubika_alert_index_key, rubika_alert_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("core_engine.services.rubika_alerts")

_SECRET_PATTERNS = (
    re.compile(r"(?i)(session[_-]?token|auth[_-]?token|access[_-]?token|refresh[_-]?token)"),
    re.compile(r"(?i)(password|passwd|otp|pass[_-]?key|private[_-]?key|ciphertext)"),
    re.compile(r"(?i)(bearer\s+[a-z0-9\-._~+/]+=*)"),
    re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----)"),
)


class RubikaAlertType(str, Enum):
    ACCOUNT_DEGRADED = "ACCOUNT_DEGRADED"
    ACCOUNT_QUARANTINED = "ACCOUNT_QUARANTINED"
    SESSION_REQUIRES_LOGIN = "SESSION_REQUIRES_LOGIN"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    CIRCUIT_HALF_OPEN = "CIRCUIT_HALF_OPEN"
    SYSTEM_DEPENDENCY_FAILURE = "SYSTEM_DEPENDENCY_FAILURE"
    HIGH_FAILURE_RATE = "HIGH_FAILURE_RATE"
    ACCOUNT_RECOVERED = "ACCOUNT_RECOVERED"


class RubikaAlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class RubikaAlert:
    alert_id: str
    type: str
    severity: str
    title: str
    message: str
    created_at: str
    first_seen: str
    last_seen: str
    occurrence_count: int
    dedupe_key: str
    status: str = "OPEN"  # OPEN | ACKNOWLEDGED | RESOLVED
    account_id: int | None = None
    incident_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AlertSink(Protocol):
    async def send(self, alert: RubikaAlert) -> None: ...


class LoggingAlertSink:
    """Default sink — logs only; no external credentials."""

    async def send(self, alert: RubikaAlert) -> None:
        logger.info(
            "event=rubika_alert type=%s severity=%s account_id=%s dedupe=%s count=%s",
            alert.type,
            alert.severity,
            alert.account_id,
            alert.dedupe_key,
            alert.occurrence_count,
        )


class NoOpAlertSink:
    async def send(self, alert: RubikaAlert) -> None:
        return None


def redact_secrets(value: Any) -> Any:
    """Recursively redact secret-looking strings from alert payloads."""
    if value is None:
        return None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if any(
                token in key_l
                for token in (
                    "token",
                    "password",
                    "otp",
                    "secret",
                    "private_key",
                    "ciphertext",
                    "auth",
                    "pass_key",
                )
            ):
                out[key] = "[REDACTED]"
            else:
                out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        text = value
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text
    return value


def alert_dedupe_key(
    *,
    alert_type: str,
    account_id: int | None = None,
    incident_id: str | None = None,
    category: str | None = None,
) -> str:
    scope = str(account_id) if account_id is not None else "system"
    inc = incident_id or "none"
    cat = category or "none"
    return f"{alert_type}:{scope}:{inc}:{cat}"


def _now_iso(clock: datetime | None = None) -> str:
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat()


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def upsert_alert(
    redis: "Redis",
    *,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    account_id: int | None = None,
    incident_id: str | None = None,
    category: str | None = None,
    metadata: dict[str, Any] | None = None,
    sink: AlertSink | None = None,
    clock: datetime | None = None,
    ttl_seconds: int = 86400,
) -> RubikaAlert:
    """Create or bump an unresolved alert (dedupe by type+scope+incident/category)."""
    dedupe = alert_dedupe_key(
        alert_type=alert_type,
        account_id=account_id,
        incident_id=incident_id,
        category=category,
    )
    key = rubika_alert_key(dedupe)
    now = _now_iso(clock)
    safe_meta = redact_secrets(metadata or {})
    safe_title = str(redact_secrets(title))
    safe_message = str(redact_secrets(message))

    raw = await redis.get(key)
    if raw:
        data = json.loads(_decode(raw))
        if data.get("status") in ("OPEN", "ACKNOWLEDGED"):
            data["occurrence_count"] = int(data.get("occurrence_count") or 1) + 1
            data["last_seen"] = now
            data["severity"] = severity
            data["title"] = safe_title
            data["message"] = safe_message
            if incident_id:
                data["incident_id"] = incident_id
            if safe_meta:
                data["metadata"] = safe_meta
            await redis.set(key, json.dumps(data), ex=ttl_seconds)
            alert = RubikaAlert(**data)
            if sink is not None:
                await sink.send(alert)
            return alert

    alert = RubikaAlert(
        alert_id=uuid.uuid4().hex[:16],
        type=alert_type,
        severity=severity,
        title=safe_title,
        message=safe_message,
        created_at=now,
        first_seen=now,
        last_seen=now,
        occurrence_count=1,
        dedupe_key=dedupe,
        status="OPEN",
        account_id=account_id,
        incident_id=incident_id,
        metadata=safe_meta if isinstance(safe_meta, dict) else {},
    )
    await redis.set(key, json.dumps(alert.to_dict()), ex=ttl_seconds)
    await redis.sadd(rubika_alert_index_key(), dedupe)
    logger.warning(
        "event=rubika_alert_opened type=%s severity=%s account_id=%s",
        alert_type,
        severity,
        account_id,
    )
    if sink is None:
        sink = LoggingAlertSink()
    await sink.send(alert)
    return alert


async def resolve_alert(
    redis: "Redis",
    *,
    alert_type: str,
    account_id: int | None = None,
    incident_id: str | None = None,
    category: str | None = None,
    clock: datetime | None = None,
) -> RubikaAlert | None:
    dedupe = alert_dedupe_key(
        alert_type=alert_type,
        account_id=account_id,
        incident_id=incident_id,
        category=category,
    )
    key = rubika_alert_key(dedupe)
    raw = await redis.get(key)
    if not raw:
        return None
    data = json.loads(_decode(raw))
    data["status"] = "RESOLVED"
    data["last_seen"] = _now_iso(clock)
    await redis.set(key, json.dumps(data), ex=86400)
    await redis.srem(rubika_alert_index_key(), dedupe)
    return RubikaAlert(**data)


async def acknowledge_alert(
    redis: "Redis",
    *,
    dedupe_key: str,
    clock: datetime | None = None,
) -> RubikaAlert | None:
    key = rubika_alert_key(dedupe_key)
    raw = await redis.get(key)
    if not raw:
        return None
    data = json.loads(_decode(raw))
    if data.get("status") == "RESOLVED":
        return RubikaAlert(**data)
    data["status"] = "ACKNOWLEDGED"
    data["last_seen"] = _now_iso(clock)
    await redis.set(key, json.dumps(data), ex=86400)
    return RubikaAlert(**data)


async def list_open_alerts(redis: "Redis") -> list[RubikaAlert]:
    members = await redis.smembers(rubika_alert_index_key())
    out: list[RubikaAlert] = []
    for member in members or []:
        dedupe = _decode(member)
        raw = await redis.get(rubika_alert_key(dedupe))
        if not raw:
            await redis.srem(rubika_alert_index_key(), dedupe)
            continue
        data = json.loads(_decode(raw))
        if data.get("status") in ("OPEN", "ACKNOWLEDGED"):
            out.append(RubikaAlert(**data))
    severity_rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}

    def _ts(iso: str) -> float:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    out.sort(key=lambda a: (severity_rank.get(a.severity, 9), -_ts(a.last_seen)))
    return out


async def sync_alerts_from_protection(
    redis: "Redis",
    *,
    circuit_state: str,
    circuit_reason: str | None,
    accounts: list[dict[str, Any]],
    incidents: list[dict[str, Any]],
    redis_ok: bool = True,
    sink: AlertSink | None = None,
    clock: datetime | None = None,
) -> list[RubikaAlert]:
    """Materialize in-app alerts from current protection evidence (idempotent upsert)."""
    sink = sink or LoggingAlertSink()

    if not redis_ok:
        await upsert_alert(
            redis,
            alert_type=RubikaAlertType.SYSTEM_DEPENDENCY_FAILURE.value,
            severity=RubikaAlertSeverity.CRITICAL.value,
            title="وابستگی Redis در دسترس نیست",
            message="اتصال Redis برای بررسی ایمنی در دسترس نیست؛ ارسال متوقف شده است.",
            category="redis",
            metadata={"dependency": "redis"},
            sink=sink,
            clock=clock,
        )
    else:
        await resolve_alert(
            redis,
            alert_type=RubikaAlertType.SYSTEM_DEPENDENCY_FAILURE.value,
            category="redis",
            clock=clock,
        )

    if circuit_state == "open":
        await upsert_alert(
            redis,
            alert_type=RubikaAlertType.CIRCUIT_OPEN.value,
            severity=RubikaAlertSeverity.CRITICAL.value,
            title="Circuit Breaker باز است",
            message="ارسال روبیکا به دلیل باز بودن Circuit Breaker متوقف شده است.",
            category="circuit",
            metadata={"reason": circuit_reason, "state": circuit_state},
            sink=sink,
            clock=clock,
        )
        await resolve_alert(
            redis,
            alert_type=RubikaAlertType.CIRCUIT_HALF_OPEN.value,
            category="circuit",
            clock=clock,
        )
    elif circuit_state == "half_open":
        await upsert_alert(
            redis,
            alert_type=RubikaAlertType.CIRCUIT_HALF_OPEN.value,
            severity=RubikaAlertSeverity.WARNING.value,
            title="Circuit در حالت بررسی بازیابی",
            message="ارسال سراسری روبیکا در حالت HALF_OPEN با بودجه probe محدود است.",
            category="circuit",
            metadata={"reason": circuit_reason, "state": circuit_state},
            sink=sink,
            clock=clock,
        )
        await resolve_alert(
            redis,
            alert_type=RubikaAlertType.CIRCUIT_OPEN.value,
            category="circuit",
            clock=clock,
        )
    else:
        await resolve_alert(
            redis,
            alert_type=RubikaAlertType.CIRCUIT_OPEN.value,
            category="circuit",
            clock=clock,
        )
        await resolve_alert(
            redis,
            alert_type=RubikaAlertType.CIRCUIT_HALF_OPEN.value,
            category="circuit",
            clock=clock,
        )

    incident_by_account: dict[int, str] = {}
    for inc in incidents:
        aid = inc.get("account_id")
        if aid is not None:
            incident_by_account[int(aid)] = str(inc.get("incident_id") or "")

    for acct in accounts:
        account_id = int(acct["account_id"])
        health = str(acct.get("health_state") or "")
        account_status = str(acct.get("account_status") or "")
        failure_rate = float(acct.get("failure_rate") or 0)
        incident_id = incident_by_account.get(account_id) or None

        if health == "quarantined":
            await upsert_alert(
                redis,
                alert_type=RubikaAlertType.ACCOUNT_QUARANTINED.value,
                severity=RubikaAlertSeverity.CRITICAL.value,
                title=f"اکانت {account_id} قرنطینه شد",
                message="اکانت به دلیل خطاهای متوالی قرنطینه شده است.",
                account_id=account_id,
                incident_id=incident_id,
                category="quarantine",
                metadata={
                    "quarantine_reason": acct.get("quarantine_reason"),
                    "health_state": health,
                },
                sink=sink,
                clock=clock,
            )
        else:
            await resolve_alert(
                redis,
                alert_type=RubikaAlertType.ACCOUNT_QUARANTINED.value,
                account_id=account_id,
                category="quarantine",
                clock=clock,
            )

        if account_status == "requires_login" or health == "critical":
            if account_status == "requires_login":
                await upsert_alert(
                    redis,
                    alert_type=RubikaAlertType.SESSION_REQUIRES_LOGIN.value,
                    severity=RubikaAlertSeverity.WARNING.value,
                    title=f"اکانت {account_id} نیاز به ورود مجدد دارد",
                    message="اکانت نیاز به ورود مجدد دارد.",
                    account_id=account_id,
                    incident_id=incident_id,
                    category="session",
                    metadata={"health_state": health, "account_status": account_status},
                    sink=sink,
                    clock=clock,
                )
        else:
            await resolve_alert(
                redis,
                alert_type=RubikaAlertType.SESSION_REQUIRES_LOGIN.value,
                account_id=account_id,
                category="session",
                clock=clock,
            )

        if health == "degraded":
            await upsert_alert(
                redis,
                alert_type=RubikaAlertType.ACCOUNT_DEGRADED.value,
                severity=RubikaAlertSeverity.WARNING.value,
                title=f"اکانت {account_id} افت کیفیت دارد",
                message="کیفیت ارسال اکانت افت کرده است؛ نظارت کنید.",
                account_id=account_id,
                incident_id=incident_id,
                category="health",
                metadata={"health_state": health, "failure_rate": failure_rate},
                sink=sink,
                clock=clock,
            )
        else:
            await resolve_alert(
                redis,
                alert_type=RubikaAlertType.ACCOUNT_DEGRADED.value,
                account_id=account_id,
                category="health",
                clock=clock,
            )

        if failure_rate >= 0.5 and int(acct.get("failures_window") or 0) >= 5:
            await upsert_alert(
                redis,
                alert_type=RubikaAlertType.HIGH_FAILURE_RATE.value,
                severity=RubikaAlertSeverity.WARNING.value,
                title=f"نرخ خطای بالای اکانت {account_id}",
                message="نرخ خطای ارسال در پنجره اخیر بالاست.",
                account_id=account_id,
                incident_id=incident_id,
                category="failure_rate",
                metadata={"failure_rate": failure_rate},
                sink=sink,
                clock=clock,
            )
        else:
            await resolve_alert(
                redis,
                alert_type=RubikaAlertType.HIGH_FAILURE_RATE.value,
                account_id=account_id,
                category="failure_rate",
                clock=clock,
            )

    return await list_open_alerts(redis)
