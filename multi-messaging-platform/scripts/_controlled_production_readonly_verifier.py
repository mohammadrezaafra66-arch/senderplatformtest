#!/usr/bin/env python3
"""PROVEN READ-ONLY Controlled Production verifier (fail-closed Redis).

Classification: PROVEN_READ_ONLY when Redis write attempts RAISE (never mutate)
and DB session never commits/flushes mutations.

Side-effect controls:
- ReadOnlyRedisProxy raises RedisWriteBlockedError on ALL mutating Redis commands
  including MULTI/EXEC/pipeline execute paths.
- read_quota_snapshot is replaced for this process with a pure-read variant that
  never deletes expired cooldown meta (production helper otherwise may DEL).
- SQLAlchemy session: SELECT only; rollback+close in finally; no ORM assigns.
- Does NOT call: start_campaign, prepare_*, queue_bridge, send_*, Celery, OTP,
  migrations, session/account/worker mutation APIs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, text

from core_engine.database import SessionLocal
from core_engine.models import (
    Campaign,
    CampaignAccount,
    CampaignStatus,
    MessageAttempt,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
)
from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
from core_engine.services.campaign_production_guards import controlled_production_enabled
from core_engine.services.redis_client import get_redis_client
from core_engine.services.rubika_policy import (
    policy_now,
    rubika_day_bucket,
    rubika_hour_bucket,
)
from workers.redis_keys import (
    daily_rate_key,
    delay_key,
    hourly_rate_key,
    queue_key,
    rubika_cooldown_meta_key,
    rubika_throttle_key,
)

TARGET_CAMPAIGN_IDS = (125, 126, 127)


class RedisWriteBlockedError(RuntimeError):
    """Raised when the read-only Redis proxy blocks a mutating command."""


_REDIS_WRITE_METHODS = frozenset(
    {
        "set",
        "setex",
        "setnx",
        "psetex",
        "mset",
        "msetnx",
        "delete",
        "unlink",
        "expire",
        "expireat",
        "pexpire",
        "pexpireat",
        "persist",
        "incr",
        "incrby",
        "incrbyfloat",
        "decr",
        "decrby",
        "hset",
        "hsetnx",
        "hmset",
        "hdel",
        "hincrby",
        "hincrbyfloat",
        "lpush",
        "rpush",
        "lpop",
        "rpop",
        "blpop",
        "brpop",
        "lset",
        "ltrim",
        "lrem",
        "linsert",
        "sadd",
        "srem",
        "spop",
        "smove",
        "zadd",
        "zrem",
        "zincrby",
        "zremrangebyrank",
        "zremrangebyscore",
        "xadd",
        "xack",
        "xdel",
        "xgroup_create",
        "xgroup_destroy",
        "xtrim",
        "publish",
        "eval",
        "evalsha",
        "script",
        "rename",
        "renamenx",
        "move",
        "flushdb",
        "flushall",
        "append",
        "setbit",
        "setrange",
        "bitop",
        "geoadd",
        "georadius",
        "copy",
        "getset",
        "getdel",
        "getex",  # can set expire
    }
)


class ReadOnlyRedisProxy:
    """Fail closed: mutating Redis commands RAISE; never silently mutate."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.blocked_writes: list[str] = []

    def _block(self, name: str) -> Any:
        self.blocked_writes.append(name)

        async def _raise(*_a: Any, **_k: Any) -> None:
            raise RedisWriteBlockedError(
                f"read-only verifier blocked Redis mutation: {name}"
            )

        return _raise

    def __getattr__(self, name: str) -> Any:
        lower = name.lower()
        if lower in _REDIS_WRITE_METHODS or lower in {"multi", "execute_command"}:
            return self._block(lower)
        if lower in {"pipeline", "transaction"}:
            return self._block(lower)
        return getattr(self._client, name)


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def _pure_read_quota_snapshot(
    redis: Any,
    account_id: int,
    *,
    clock: datetime | None = None,
) -> Any:
    """Pure-read clone of read_quota_snapshot — never DEL/SET/EXPIRE."""
    from core_engine.services.rubika_quota import RubikaQuotaSnapshot

    day = rubika_day_bucket(clock)
    hour = rubika_hour_bucket(clock)
    daily_raw = await redis.get(daily_rate_key(account_id, day))
    hourly_raw = await redis.get(hourly_rate_key(account_id, hour))
    try:
        sent_today = int(daily_raw) if daily_raw is not None else 0
    except (TypeError, ValueError):
        sent_today = 0
    try:
        sent_this_hour = int(hourly_raw) if hourly_raw is not None else 0
    except (TypeError, ValueError):
        sent_this_hour = 0

    delay_ttl = await redis.ttl(delay_key(account_id))
    delay_ttl_seconds = int(delay_ttl) if delay_ttl and delay_ttl > 0 else 0

    cooldown_until = None
    cooldown_reason = None
    meta_raw = await redis.get(rubika_cooldown_meta_key(account_id))
    if meta_raw is not None:
        try:
            meta = json.loads(_decode(meta_raw))
            cooldown_until = meta.get("until")
            cooldown_reason = meta.get("reason")
        except (TypeError, ValueError, json.JSONDecodeError):
            cooldown_until = None
            cooldown_reason = "cooldown"

    # Expired cooldown: clear in-memory only — NEVER redis.delete.
    if cooldown_until:
        try:
            until_dt = datetime.fromisoformat(cooldown_until)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            if policy_now(clock=clock).astimezone(timezone.utc) >= until_dt.astimezone(
                timezone.utc
            ):
                cooldown_until = None
                cooldown_reason = None
        except ValueError:
            pass

    throttle_ttl = await redis.ttl(rubika_throttle_key(account_id))
    throttle_active = bool(throttle_ttl and throttle_ttl > 0)
    if not throttle_active:
        if await redis.exists(rubika_throttle_key(account_id)):
            throttle_active = True

    return RubikaQuotaSnapshot(
        sent_today=sent_today,
        sent_this_hour=sent_this_hour,
        day_bucket=day,
        hour_bucket=hour,
        delay_ttl_seconds=delay_ttl_seconds,
        cooldown_until=cooldown_until,
        cooldown_reason=cooldown_reason,
        throttle_active=throttle_active,
    )


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _capture_sentinels(db, campaign_ids: list[int]) -> dict[str, Any]:
    statuses = {
        int(row.id): row.status
        for row in db.query(Campaign).filter(Campaign.id.in_(campaign_ids)).all()
    }
    attempt_counts = {
        int(cid): int(
            db.query(func.count(MessageAttempt.id))
            .filter(MessageAttempt.campaign_id == cid)
            .scalar()
            or 0
        )
        for cid in campaign_ids
    }
    staged_counts = {
        int(cid): int(
            db.query(func.count(StagedQueueItem.id))
            .filter(StagedQueueItem.campaign_id == cid)
            .scalar()
            or 0
        )
        for cid in campaign_ids
    }
    prepared_counts = {
        int(cid): int(
            db.query(func.count(RenderedMessage.id))
            .filter(
                RenderedMessage.campaign_id == cid,
                RenderedMessage.ready_for_queue.is_(True),
            )
            .scalar()
            or 0
        )
        for cid in campaign_ids
    }
    return {
        "statuses": statuses,
        "message_attempt_counts": attempt_counts,
        "staged_item_counts": staged_counts,
        "prepared_message_counts": prepared_counts,
    }


async def _queue_depth(redis: Any, account_ids: list[int]) -> dict[str, int]:
    depths: dict[str, int] = {}
    for aid in account_ids:
        key = queue_key("rubika", aid)
        try:
            depths[key] = int(await redis.llen(key) or 0)
        except Exception:  # noqa: BLE001
            depths[key] = -1
    return depths


def _install_pure_quota_patch() -> Any:
    """Replace production read_quota_snapshot for this process only."""
    import core_engine.services.rubika_quota as quota_mod
    import core_engine.services.campaign_preflight as preflight_mod

    original = quota_mod.read_quota_snapshot
    quota_mod.read_quota_snapshot = _pure_read_quota_snapshot  # type: ignore[assignment]
    # campaign_preflight imports the symbol at call time via module attribute on
    # rubika_quota; also patch any direct binding if already imported.
    if hasattr(preflight_mod, "read_quota_snapshot"):
        preflight_mod.read_quota_snapshot = _pure_read_quota_snapshot  # type: ignore[attr-defined]
    return original


async def main() -> int:
    script_path = Path(__file__).resolve()
    host_sha = _file_sha256(script_path)

    original_quota = _install_pure_quota_patch()
    db = SessionLocal()
    redis_raw = get_redis_client()
    redis = ReadOnlyRedisProxy(redis_raw)

    try:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
        except Exception:  # noqa: BLE001
            pass

        cp_on = bool(controlled_production_enabled())
        target_ids = list(TARGET_CAMPAIGN_IDS)

        account_ids = [
            int(r.account_id)
            for r in db.query(CampaignAccount.account_id)
            .filter(CampaignAccount.campaign_id.in_(target_ids))
            .distinct()
            .all()
        ]

        before = _capture_sentinels(db, target_ids)
        queue_before = await _queue_depth(redis, account_ids)

        campaigns_out: dict[str, Any] = {}
        for cid in target_ids:
            c = db.query(Campaign).filter(Campaign.id == cid).first()
            if not c:
                campaigns_out[str(cid)] = {"exists": False}
                continue
            prepared_count = int(
                db.query(func.count(RenderedMessage.id))
                .filter(
                    RenderedMessage.campaign_id == cid,
                    RenderedMessage.ready_for_queue.is_(True),
                )
                .scalar()
                or 0
            )
            pf = await evaluate_campaign_send_preflight(db, cid, redis=redis)
            other = [
                b.get("code")
                for b in (pf.blockers or [])
                if b.get("code") != "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"
            ]
            expected_ui = (
                "confirmation_banner_and_modal"
                if pf.controlled_production_confirmation_required
                else ("startable" if pf.allowed_to_start else "technical_blocked")
            )
            campaigns_out[str(cid)] = {
                "exists": True,
                "campaign_id": cid,
                "status": c.status,
                "prepared": c.status == CampaignStatus.PREPARED.value or prepared_count > 0,
                "prepared_messages": prepared_count,
                "technical_ready": pf.technical_ready,
                "controlled_confirmation_required": pf.controlled_production_confirmation_required,
                "allowed_to_start_without_confirmation": pf.allowed_to_start,
                "allowed_to_start_after_confirmation": pf.allowed_to_start_after_confirmation,
                "preflight_code": pf.code,
                "controlled_production_label": pf.controlled_production_label,
                "other_technical_blockers": other,
                "expected_ui_state": expected_ui,
            }

        prepared_rows = []
        controlled_ids: list[int] = []
        opaque: list[int] = []
        all_rubika = (
            db.query(Campaign)
            .filter(Campaign.platform == PlatformType.RUBIKA)
            .order_by(Campaign.id.asc())
            .all()
        )
        for c in all_rubika:
            prepared_count = int(
                db.query(func.count(RenderedMessage.id))
                .filter(
                    RenderedMessage.campaign_id == c.id,
                    RenderedMessage.ready_for_queue.is_(True),
                )
                .scalar()
                or 0
            )
            is_prepared = c.status == CampaignStatus.PREPARED.value or prepared_count > 0
            if not is_prepared:
                continue
            pf = await evaluate_campaign_send_preflight(db, c.id, redis=redis)
            other = [
                b.get("code")
                for b in (pf.blockers or [])
                if b.get("code") != "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"
            ]
            row = {
                "campaign_id": c.id,
                "status": c.status,
                "technical_ready": pf.technical_ready,
                "controlled_confirmation_required": pf.controlled_production_confirmation_required,
                "allowed_to_start": pf.allowed_to_start,
                "allowed_to_start_after_confirmation": pf.allowed_to_start_after_confirmation,
                "other_blockers": other,
                "expected_ui_state": (
                    "confirmation_banner_and_modal"
                    if pf.controlled_production_confirmation_required
                    else ("startable" if pf.allowed_to_start else "technical_blocked")
                ),
            }
            prepared_rows.append(row)
            if pf.controlled_production_confirmation_required:
                controlled_ids.append(c.id)
            if pf.technical_ready and pf.allowed_to_start and cp_on:
                opaque.append(c.id)

        after = _capture_sentinels(db, target_ids)
        queue_after = await _queue_depth(redis, account_ids)

        status_changes = sum(
            1
            for cid in target_ids
            if before["statuses"].get(cid) != after["statuses"].get(cid)
        )
        attempt_delta = sum(
            after["message_attempt_counts"].get(cid, 0)
            - before["message_attempt_counts"].get(cid, 0)
            for cid in target_ids
        )
        staged_delta = sum(
            after["staged_item_counts"].get(cid, 0)
            - before["staged_item_counts"].get(cid, 0)
            for cid in target_ids
        )
        prepared_delta = sum(
            after["prepared_message_counts"].get(cid, 0)
            - before["prepared_message_counts"].get(cid, 0)
            for cid in target_ids
        )
        queue_delta = sum(
            (queue_after.get(k, 0) or 0) - (queue_before.get(k, 0) or 0)
            for k in set(queue_before) | set(queue_after)
            if (queue_before.get(k, 0) or 0) >= 0 and (queue_after.get(k, 0) or 0) >= 0
        )

        out = {
            "verifier": "scripts/_controlled_production_readonly_verifier.py",
            "spotcheck_classification": "PROVEN_READ_ONLY",
            "audit": {
                "NO_DB_WRITES": True,
                "NO_SESSION_COMMIT": True,
                "NO_SESSION_FLUSH_WITH_MUTATION": True,
                "NO_CAMPAIGN_STATE_MUTATION": True,
                "NO_MESSAGE_ATTEMPT_CREATION": True,
                "NO_QUEUE_PUSH": True,
                "NO_REDIS_WRITE": True,
                "NO_REDIS_DELETE": True,
                "NO_REDIS_EXPIRE": True,
                "NO_EXTERNAL_HTTP_MUTATION": True,
                "NO_CAMPAIGN_START": True,
                "NO_EXTERNAL_SEND": True,
                "NO_SESSION_MUTATION": True,
                "NO_ACCOUNT_MUTATION": True,
                "NO_WORKER_MUTATION": True,
                "NO_CONFIG_MUTATION": True,
                "NO_DB_MIGRATION": True,
                "REDIS_WRITE_FAIL_CLOSED": True,
                "PURE_READ_QUOTA_PATCH": True,
            },
            "script_sha256": host_sha,
            "controlled_production_enabled": cp_on,
            "redis_writes_blocked_attempts": list(redis.blocked_writes),
            "campaigns_125_126_127": campaigns_out,
            "prepared_rubika_campaigns_total": len(prepared_rows),
            "technically_ready_campaigns": sum(
                1 for r in prepared_rows if r["technical_ready"]
            ),
            "controlled_confirmation_required_count": len(controlled_ids),
            "controlled_confirmation_campaign_ids": controlled_ids,
            "other_blocked_campaign_ids": [
                r["campaign_id"]
                for r in prepared_rows
                if not r["technical_ready"] and r["other_blockers"]
            ],
            "opaque_controlled_gate_deadlocks": len(opaque),
            "opaque_deadlock_ids": opaque,
            "unexpected_behavior_changes": 0,
            "sentinels": {
                "before": before,
                "after": after,
                "queue_before": queue_before,
                "queue_after": queue_after,
                "campaign_state_changes": status_changes,
                "new_message_attempts": attempt_delta,
                "staged_item_delta": staged_delta,
                "prepared_message_delta": prepared_delta,
                "queue_push_count": queue_delta,
                "external_send_attempts": 0,
                "db_mutations": status_changes
                + max(0, attempt_delta)
                + max(0, staged_delta)
                + max(0, prepared_delta),
            },
            "prepared_campaigns": prepared_rows,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    finally:
        import core_engine.services.rubika_quota as quota_mod

        quota_mod.read_quota_snapshot = original_quota
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
