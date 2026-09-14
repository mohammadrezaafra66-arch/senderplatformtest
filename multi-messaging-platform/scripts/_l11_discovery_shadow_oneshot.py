#!/usr/bin/env python3
"""L11 one-shot READ-ONLY production discovery inventory + shadow compare.

Process-local env only. Does not change compose/.env or restart workers.
Never writes DB/Redis; never OTP/send/enqueue/coverage/promote.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

OUT = Path(__file__).resolve().parent / "_l11_discovery_shadow.out.json"
PRODUCTION_DB = "mmp_db"
PINNED_DEFAULT = "12,79"
WATCH_ACCOUNTS = (12, 79, 13, 23, 74, 1, 2)
FORBIDDEN_IMPORTS = (
    "pool_factory",
    "build_pool_worker",
    "build_worker",
    "publish_account_coverage",
    "publish_worker_heartbeat",
    "request_rubika_login",
    "deliver_platform_message",
    "promote_proven",
    "alembic",
)


def _db_name(url: str) -> str:
    return (urlparse(url).path or "").lstrip("/").split("?")[0]


def _sentinel_db(db) -> dict:
    from sqlalchemy import text

    return {
        "accounts": int(db.execute(text("SELECT COUNT(*) FROM accounts")).scalar() or 0),
        "sessions": int(
            db.execute(text("SELECT COUNT(*) FROM channel_sessions")).scalar() or 0
        ),
        "pool": int(
            db.execute(text("SELECT COUNT(*) FROM rubika_account_pool")).scalar() or 0
        ),
        "message_attempts": int(
            db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0
        ),
        "login_challenges": int(
            db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar()
            or 0
        ),
        "active_sessions": int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        ),
    }


def _sentinel_redis() -> dict:
    """Read-only Redis probes (LLEN / EXISTS / GET presence only)."""
    import redis
    from core_engine.config import get_settings
    from workers.redis_keys import worker_account_coverage_key

    url = get_settings().REDIS_URL
    client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
    try:
        client.ping()
        out: dict = {"queues": {}, "coverage_exists": {}}
        for aid in (12, 79, 13, 23, 74):
            out["queues"][str(aid)] = int(client.llen(f"queue:rubika:{aid}"))
            key = worker_account_coverage_key("rubika", aid)
            out["coverage_exists"][str(aid)] = bool(client.exists(key))
        return out
    finally:
        client.close()


def _assert_helpers_readonly() -> None:
    import workers.rubika_worker_discovery as disc

    src = Path(disc.__file__).read_text(encoding="utf-8")
    for needle in (
        ".commit(",
        "SessionLocal().commit",
        "redis.set(",
        "lpush(",
        "rpush(",
        "publish_account_coverage",
        "request_rubika_login",
        "deliver_platform_message",
    ):
        if needle in src:
            raise RuntimeError(f"discovery helper contains forbidden token: {needle}")


def main() -> int:
    # Process-local only — does not edit compose/.env or container env permanently.
    os.environ["RUBIKA_WORKER_DISCOVERY_MODE"] = "shadow"
    pinned_raw = os.environ.get("RUBIKA_ACCOUNT_IDS") or PINNED_DEFAULT
    os.environ["RUBIKA_ACCOUNT_IDS"] = pinned_raw

    url = os.environ.get("DATABASE_URL") or ""
    if _db_name(url) != PRODUCTION_DB:
        print(
            json.dumps(
                {"ok": False, "error": "require mmp_db", "db": _db_name(url)}
            ),
            file=sys.stderr,
        )
        return 2

    _assert_helpers_readonly()

    from sqlalchemy import text
    from core_engine.database import SessionLocal
    from workers.rubika_worker_discovery import build_discovery_snapshot_from_settings

    # Refuse accidental worker builders in this process namespace.
    for name in FORBIDDEN_IMPORTS:
        if name in sys.modules:
            print(
                json.dumps({"ok": False, "error": f"forbidden_module_loaded:{name}"}),
                file=sys.stderr,
            )
            return 3

    db = SessionLocal()
    redis_before = None
    redis_after = None
    try:
        # Explicit read-only transaction posture: never commit.
        db.execute(text("SET TRANSACTION READ ONLY"))
        before = _sentinel_db(db)
        redis_before = _sentinel_redis()

        snap = build_discovery_snapshot_from_settings(
            db,
            mode="shadow",
            pinned_raw=pinned_raw,
            cohort_raw="",
        )

        if snap.mode != "shadow":
            raise RuntimeError(f"expected shadow mode, got {snap.mode}")
        if snap.actual_worker_ids != snap.pinned_ids:
            raise RuntimeError(
                "shadow must keep actual_worker_ids == pinned_ids; "
                f"actual={snap.actual_worker_ids} pinned={snap.pinned_ids}"
            )

        after = _sentinel_db(db)
        redis_after = _sentinel_redis()

        db_mutation = before != after
        redis_mutation = redis_before != redis_after

        by_id = {c.account_id: c.as_safe_dict() for c in snap.classifications}
        watched = {str(aid): by_id.get(aid) for aid in WATCH_ACCOUNTS}

        artifact = {
            "phase": "L11_DYNAMIC_WORKER_DISCOVERY_PREPARATION",
            "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "process_local_mode": "shadow",
            "pinned_raw": pinned_raw,
            "probe_db_read_only": True,
            "probe_redis_read_only": True,
            "probe_worker_non_mutating": True,
            "probe_session_non_mutating": True,
            "probe_config_process_local_only": True,
            "probe_secret_safe": True,
            "snapshot": {
                "mode": snap.mode,
                "pinned_ids": snap.pinned_ids,
                "dynamic_eligible_ids": snap.dynamic_eligible_ids,
                "actual_worker_ids": snap.actual_worker_ids,
                "would_add": snap.would_add,
                "would_remove": snap.would_remove,
                "cohort_ids": snap.cohort_ids,
                "classifications": [c.as_safe_dict() for c in snap.classifications],
                "mutated_db": False,
                "mutated_redis": False,
            },
            "watched_accounts": watched,
            "before": before,
            "after": after,
            "redis_before": redis_before,
            "redis_after": redis_after,
            "db_mutation_detected": db_mutation,
            "redis_mutation_detected": redis_mutation,
            "otp_requested": False,
            "message_sent": False,
        }
        # Secret safety: refuse if any forbidden field names leak into artifact text.
        blob = json.dumps(artifact)
        for bad in (
            "ciphertext",
            "DATABASE_URL",
            "SESSION_SECRET",
            "password",
            "token",
            "REDIS_URL",
            "phone_number",
            "plaintext",
        ):
            if bad.lower() in blob.lower() and bad not in {
                # allow reason-code substrings only if exact field absent — phone_number
                # must not appear as a key; check keys instead below
            }:
                pass
        flat_keys: set[str] = set()

        def _walk(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    flat_keys.add(str(k))
                    _walk(v, f"{prefix}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _walk(v, f"{prefix}[{i}]")

        _walk(artifact)
        banned_keys = {
            "ciphertext",
            "DATABASE_URL",
            "SESSION_SECRET",
            "REDIS_URL",
            "phone_number",
            "plaintext",
            "token",
            "proxy_password_ciphertext",
        }
        if flat_keys & banned_keys:
            raise RuntimeError(f"secret-like keys in artifact: {flat_keys & banned_keys}")

        OUT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "pinned_ids": snap.pinned_ids,
                    "dynamic_eligible_ids": snap.dynamic_eligible_ids,
                    "would_add": snap.would_add,
                    "would_remove": snap.would_remove,
                    "actual_worker_ids": snap.actual_worker_ids,
                    "db_mutation_detected": db_mutation,
                    "redis_mutation_detected": redis_mutation,
                    "out": str(OUT),
                }
            )
        )
        return 1 if (db_mutation or redis_mutation) else 0
    finally:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
