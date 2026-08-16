"""Rubika Phase 4 — Redis-backed incident store (account vs system).

No DB migration. Incidents live in Redis with an open-index set.
Dedup key prevents duplicate storms; occurrence_count increments.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from workers.redis_keys import rubika_incident_index_key, rubika_incident_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("core_engine.services.rubika_incidents")


@dataclass
class RubikaIncident:
    incident_id: str
    scope: str  # ACCOUNT | SYSTEM
    account_id: int | None
    category: str
    severity: str
    status: str  # OPEN | RESOLVED
    opened_at: str
    last_seen_at: str
    resolved_at: str | None
    reason: str
    source: str
    occurrence_count: int
    code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def incident_dedupe_key(
    *,
    scope: str,
    category: str,
    code: str | None,
    account_id: int | None,
) -> str:
    acct = str(account_id) if account_id is not None else "none"
    return f"{scope}:{category}:{code or 'none'}:{acct}"


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


async def open_or_update_incident(
    redis: "Redis",
    *,
    scope: str,
    category: str,
    severity: str,
    reason: str,
    source: str,
    account_id: int | None = None,
    code: str | None = None,
    clock: datetime | None = None,
    ttl_seconds: int = 86400,
) -> RubikaIncident:
    dedupe = incident_dedupe_key(
        scope=scope, category=category, code=code, account_id=account_id
    )
    key = rubika_incident_key(dedupe)
    now = _now_iso(clock)
    raw = await redis.get(key)
    if raw:
        data = json.loads(_decode(raw))
        if data.get("status") == "OPEN":
            data["occurrence_count"] = int(data.get("occurrence_count") or 1) + 1
            data["last_seen_at"] = now
            data["severity"] = severity
            await redis.set(key, json.dumps(data), ex=ttl_seconds)
            logger.info(
                "event=rubika_incident_updated incident_id=%s scope=%s count=%s",
                data["incident_id"],
                scope,
                data["occurrence_count"],
            )
            return RubikaIncident(**data)

    incident = RubikaIncident(
        incident_id=uuid.uuid4().hex[:16],
        scope=scope,
        account_id=account_id,
        category=category,
        severity=severity,
        status="OPEN",
        opened_at=now,
        last_seen_at=now,
        resolved_at=None,
        reason=reason,
        source=source,
        occurrence_count=1,
        code=code,
    )
    await redis.set(key, json.dumps(incident.to_dict()), ex=ttl_seconds)
    await redis.sadd(rubika_incident_index_key(), dedupe)
    logger.warning(
        "event=rubika_incident_opened incident_id=%s scope=%s category=%s account_id=%s",
        incident.incident_id,
        scope,
        category,
        account_id,
    )
    return incident


async def resolve_incident(
    redis: "Redis",
    *,
    scope: str,
    category: str,
    code: str | None = None,
    account_id: int | None = None,
    clock: datetime | None = None,
) -> RubikaIncident | None:
    dedupe = incident_dedupe_key(
        scope=scope, category=category, code=code, account_id=account_id
    )
    key = rubika_incident_key(dedupe)
    raw = await redis.get(key)
    if not raw:
        return None
    data = json.loads(_decode(raw))
    data["status"] = "RESOLVED"
    data["resolved_at"] = _now_iso(clock)
    await redis.set(key, json.dumps(data), ex=86400)
    await redis.srem(rubika_incident_index_key(), dedupe)
    logger.info(
        "event=rubika_incident_resolved incident_id=%s scope=%s",
        data.get("incident_id"),
        scope,
    )
    return RubikaIncident(**data)


async def list_open_incidents(redis: "Redis") -> list[RubikaIncident]:
    members = await redis.smembers(rubika_incident_index_key())
    out: list[RubikaIncident] = []
    for member in members or []:
        dedupe = _decode(member)
        raw = await redis.get(rubika_incident_key(dedupe))
        if not raw:
            await redis.srem(rubika_incident_index_key(), dedupe)
            continue
        data = json.loads(_decode(raw))
        if data.get("status") == "OPEN":
            out.append(RubikaIncident(**data))
    out.sort(key=lambda i: i.opened_at, reverse=True)
    return out
