#!/usr/bin/env python3
"""L9 Batch A one-shot read-only canonical shadow probe (13,23,74).

Process-local env only — does not persist compose/.env or restart containers.
Intended to run inside mmp_core_api:

  RUBIKA_CANONICAL_SESSION_MODE=shadow \\
  RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=13,23,74 \\
  python scripts/_l9_batch_a_shadow_oneshot.py

Never writes DB/Redis; never OTP/send/enqueue; never changes session_status.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BATCH_A = (13, 23, 74)
EXPECTED = {13: 729, 23: 725, 74: 724}
PRODUCTION_DB = "mmp_db"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# scripts/ is bind-mounted into core_api; reports/ may not be.
OUT_JSON = Path(__file__).resolve().parent / "_l9_batch_a_shadow_oneshot.out.json"
HOST_REPORT_HINT = "reports/rubika-remediation/L9_BATCH_A_SHADOW_RESULTS.json"


def _db_name(url: str) -> str:
    return (urlparse(url).path or "").lstrip("/").split("?")[0]


def _sentinel_snapshot(db) -> dict[str, Any]:
    from sqlalchemy import text

    total = int(db.execute(text("SELECT COUNT(*) FROM channel_sessions")).scalar() or 0)
    active = int(
        db.execute(
            text(
                "SELECT COUNT(*) FROM channel_sessions "
                "WHERE session_status::text='active'"
            )
        ).scalar()
        or 0
    )
    attempts = int(db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0)
    challenges = int(
        db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar() or 0
    )
    states: dict[str, str] = {}
    for aid in BATCH_A:
        rows = db.execute(
            text(
                "SELECT id||':'||session_status::text FROM channel_sessions "
                "WHERE account_id=:aid ORDER BY id"
            ),
            {"aid": aid},
        ).fetchall()
        states[str(aid)] = "\n".join(r[0] for r in rows)
    fingerprints: dict[str, str] = {}
    for aid, sid in EXPECTED.items():
        row = db.execute(
            text(
                "SELECT id||':'||account_id||':'||session_status::text||':'||"
                "COALESCE(identity_guid,'') FROM channel_sessions WHERE id=:sid"
            ),
            {"sid": sid},
        ).fetchone()
        ag = db.execute(
            text("SELECT COALESCE(rubika_guid,'') FROM accounts WHERE id=:aid"),
            {"aid": aid},
        ).scalar()
        fingerprints[str(sid)] = f"{row[0] if row else 'missing'}|acct_guid={ag}"
    return {
        "total_sessions": total,
        "active_global": active,
        "message_attempts": attempts,
        "login_challenges": challenges,
        "account_states": states,
        "session_fingerprints": fingerprints,
    }


def _redis_queue_lens() -> dict[str, int]:
    import redis
    from core_engine.config import get_settings

    url = get_settings().REDIS_URL
    client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
    try:
        client.ping()
        out = {}
        for aid in BATCH_A:
            key = f"queue:rubika:{aid}"
            out[key] = int(client.llen(key))
        return out
    finally:
        client.close()


async def _reconnect(db, account_id: int, session_id: int, expected_guid: str | None):
    from core_engine.services.rubika_candidate_prover import RealRubikaCandidateProver

    proof = await RealRubikaCandidateProver().prove_detailed(
        db,
        account_id=account_id,
        session_id=session_id,
        expected_guid=expected_guid,
    )
    return {
        "ok": bool(proof.ok),
        "code": proof.error_code or proof.proof_status,
        "identity_match": proof.identity_match is True,
        "duration_ms": proof.duration_ms,
        "sanitized_message": proof.sanitized_message,
    }


def main() -> int:
    mode = (os.environ.get("RUBIKA_CANONICAL_SESSION_MODE") or "").strip().lower()
    allow = (os.environ.get("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS") or "").strip()
    if mode != "shadow":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "require process-local RUBIKA_CANONICAL_SESSION_MODE=shadow",
                    "actual_mode": mode,
                }
            ),
            file=sys.stderr,
        )
        return 2
    if allow != "13,23,74":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "require RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=13,23,74",
                    "actual_allowlist": allow,
                }
            ),
            file=sys.stderr,
        )
        return 2

    url = os.environ.get("DATABASE_URL") or ""
    if _db_name(url) != PRODUCTION_DB:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "DATABASE_URL must target mmp_db for this probe",
                    "db": _db_name(url),
                }
            ),
            file=sys.stderr,
        )
        return 2

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from core_engine.config import get_settings
    from core_engine.services.rubika_canonical_runtime import (
        clear_shadow_hooks,
        compare_legacy_vs_canonical,
        get_shadow_metrics,
        load_rubika_runtime_session,
        register_shadow_hook,
        select_legacy_rubika_session_row,
    )
    from core_engine.services.rubika_canonical_session import load_canonical_rubika_session

    get_settings.cache_clear()
    get_shadow_metrics().reset()
    clear_shadow_hooks()
    captured: list[dict[str, Any]] = []
    register_shadow_hook(lambda c: captured.append(c.as_safe_dict()))

    engine = create_engine(url)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    artifact: dict[str, Any] = {
        "phase": "L9_BATCH_A_CANONICAL_SHADOW_VERIFICATION",
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "process_local_mode": mode,
        "process_local_allowlist": allow,
        "otp_requested": False,
        "message_sent": False,
        "accounts": {},
        "reconnect": {},
    }
    try:
        before = _sentinel_snapshot(db)
        queues_before = _redis_queue_lens()
        artifact["before"] = before
        artifact["queues_before"] = queues_before

        for aid in BATCH_A:
            legacy = select_legacy_rubika_session_row(db, aid)
            comparison = compare_legacy_vs_canonical(db, aid, legacy_row=legacy)
            # Exercise runtime shadow path (legacy authoritative); discard plaintext.
            selected = load_rubika_runtime_session(db, aid)
            loaded = load_canonical_rubika_session(
                db, aid, require_identity_binding=True, return_plaintext=False
            )
            acct_guid = db.execute(
                text("SELECT rubika_guid FROM accounts WHERE id=:aid"),
                {"aid": aid},
            ).scalar()
            recon = asyncio.run(
                _reconnect(
                    db,
                    aid,
                    int(loaded.session_id),
                    str(acct_guid).strip() if acct_guid else None,
                )
            )
            entry = {
                "account_id": aid,
                "expected_session_id": EXPECTED[aid],
                "legacy_selected_session_id": comparison.legacy_selected_session_id,
                "canonical_selected_session_id": comparison.canonical_selected_session_id,
                "canonical_error": comparison.canonical_error,
                "shadow_result": comparison.metric,
                "shadow_match": comparison.match,
                "runtime_selected_session_id": int(selected.session_id),
                "runtime_source": selected.source,
                "runtime_mode": selected.mode,
                "canonical_loader_session_id": int(loaded.session_id),
                "reconnect": recon,
                "safe_comparison": comparison.as_safe_dict(),
            }
            artifact["accounts"][str(aid)] = entry
            artifact["reconnect"][str(aid)] = recon

        after = _sentinel_snapshot(db)
        queues_after = _redis_queue_lens()
        artifact["after"] = after
        artifact["queues_after"] = queues_after
        artifact["shadow_events"] = captured
        artifact["shadow_metrics"] = dict(get_shadow_metrics().counts)

        db_mutation = before != after
        redis_mutation = queues_before != queues_after
        artifact["DB_MUTATION_DETECTED"] = bool(db_mutation)
        artifact["REDIS_MUTATION_DETECTED"] = bool(redis_mutation)
        artifact["MESSAGE_SENT"] = False
        artifact["OTP_REQUESTED"] = False

        all_match = all(
            artifact["accounts"][str(aid)]["shadow_result"] == "SHADOW_MATCH"
            and artifact["accounts"][str(aid)]["legacy_selected_session_id"] == EXPECTED[aid]
            and artifact["accounts"][str(aid)]["canonical_selected_session_id"] == EXPECTED[aid]
            and artifact["accounts"][str(aid)]["reconnect"]["ok"]
            and artifact["accounts"][str(aid)]["reconnect"]["identity_match"]
            for aid in BATCH_A
        )
        artifact["BATCH_A_SHADOW_ONE_SHOT_PASS"] = bool(
            all_match and not db_mutation and not redis_mutation
        )
        artifact["ok"] = artifact["BATCH_A_SHADOW_ONE_SHOT_PASS"]

        # Ensure no uncommitted ORM dirt
        db.rollback()
    finally:
        clear_shadow_hooks()
        get_shadow_metrics().reset()
        db.close()
        engine.dispose()
        get_settings.cache_clear()

    OUT_JSON.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    artifact["artifact_path_in_container"] = str(OUT_JSON)
    artifact["host_report_hint"] = HOST_REPORT_HINT
    print(json.dumps(artifact, indent=2))
    return 0 if artifact.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
