#!/usr/bin/env python3
"""L16 Account27 post-ENFORCE verify (DB/Redis read-only + reconnect probe)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

PILOT = 27
SID = 772
BATCH = {13: 729, 23: 725, 74: 724}
EXPECTED_WORKERS = [12, 13, 23, 27, 74, 79]
OUT = Path("/app/scripts/_l16_a27_enforce_verify.out.json")


async def main() -> dict:
    from sqlalchemy import text

    from core_engine.config import get_settings
    from core_engine.database import SessionLocal
    from core_engine.models import Account, ChannelSession, SessionType
    from core_engine.services.rubika_canonical_runtime import (
        compare_legacy_vs_canonical,
        enforce_applies_to_account,
        load_rubika_runtime_session,
        select_legacy_rubika_session_row,
    )
    from core_engine.services.rubika_canonical_session import load_canonical_rubika_session
    from core_engine.services.rubika_candidate_prover import RealRubikaCandidateProver
    from workers.config import get_worker_settings
    from workers.redis_keys import worker_account_coverage_key
    from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings
    from workers.session_access import load_account_session_plaintext
    import redis.asyncio as redis

    get_settings.cache_clear()
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        acct = db.query(Account).filter(Account.id == PILOT).first()
        rows = (
            db.query(ChannelSession)
            .filter(ChannelSession.account_id == PILOT)
            .order_by(ChannelSession.id.asc())
            .all()
        )
        active = [
            r
            for r in rows
            if (r.session_status.value if hasattr(r.session_status, "value") else str(r.session_status)).lower()
            == "active"
        ]
        guid = (acct.rubika_guid or "").strip() if acct else ""
        can = load_canonical_rubika_session(db, PILOT, require_identity_binding=True)
        can_id = int(can.session_id)
        legacy = select_legacy_rubika_session_row(db, PILOT)
        cmp = compare_legacy_vs_canonical(db, PILOT, legacy_row=legacy)
        enforce_on = enforce_applies_to_account(PILOT)

        # Runtime loader under enforce must return canonical session (not max-id legacy)
        rt = load_rubika_runtime_session(db, PILOT)
        rt_id = int(rt.session_id)
        rt_source = str(rt.source)
        # drop any plaintext held on selection object reference
        del rt

        proof = await RealRubikaCandidateProver().prove_detailed(
            db, account_id=PILOT, session_id=SID, expected_guid=guid or None
        )
        reconnect = {
            "ok": bool(proof.ok),
            "code": proof.error_code or proof.proof_status,
            "identity_match": proof.identity_match is True,
            "sanitized_message": proof.sanitized_message,
        }
        pt = load_account_session_plaintext(
            db, account_id=PILOT, session_type=SessionType.RUBIKA_SESSION
        )
        dispatch_ok = isinstance(pt, (bytes, bytearray)) and len(pt) > 0
        del pt

        global_active = int(
            db.execute(
                text("SELECT count(*) FROM channel_sessions WHERE session_status::text='active'")
            ).scalar()
            or 0
        )
        msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
        ch_count = int(
            db.execute(
                text("SELECT count(*) FROM rubika_login_challenges WHERE account_id=27")
            ).scalar()
            or 0
        )
        batch_a = {}
        for aid, _ in BATCH.items():
            batch_a[str(aid)] = db.execute(
                text(
                    "SELECT id FROM channel_sessions WHERE account_id=:a "
                    "AND session_status::text='active'"
                ),
                {"a": aid},
            ).scalar()
        a12 = db.execute(
            text(
                "SELECT string_agg(id::text || ':' || session_status::text, ',' ORDER BY id) "
                "FROM channel_sessions WHERE account_id=12"
            )
        ).scalar()
        a79 = db.execute(
            text(
                "SELECT string_agg(id::text || ':' || session_status::text, ',' ORDER BY id) "
                "FROM channel_sessions WHERE account_id=79"
            )
        ).scalar()
        session_count = len(rows)
        active_count = len(active)
        active_id = int(active[0].id) if active else None
        shadow_metric = cmp.metric
        legacy_id = int(legacy.id) if legacy is not None else None
    finally:
        db.rollback()
        db.close()

    s = get_worker_settings()
    ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
    r = redis.from_url(s.REDIS_URL, decode_responses=True)
    try:
        await r.ping()
        q = int(await r.llen(f"queue:rubika:{PILOT}"))
        cov = bool(await r.exists(worker_account_coverage_key("rubika", PILOT)))
    finally:
        await r.aclose()

    # Prefer worker-container settings; core_api may lack discovery env.
    worker_ok = True  # filled by host probe merge
    allow = str(s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS).replace(" ", "")
    mode = str(s.RUBIKA_CANONICAL_SESSION_MODE).lower()

    enforce_pass = (
        enforce_on
        and mode == "enforce"
        and allow == "13,23,27,74"
        and can_id == SID
        and active_id == SID
        and session_count == 1
        and active_count == 1
        and reconnect["ok"]
        and reconnect["identity_match"]
        and dispatch_ok
        and shadow_metric == "SHADOW_MATCH"
        and cmp.canonical_error is None
        and global_active == 4
        and msg == 5
        and q == 0
        and batch_a == {"13": 729, "23": 725, "74": 724}
        and rt_id == SID
        and rt_source == "canonical_enforce"
    )

    return {
        "PILOT_ACCOUNT_ID": PILOT,
        "PILOT_AUTHORITATIVE_SESSION": can_id,
        "PILOT_CANONICAL_ENFORCE_PASS": enforce_pass,
        "enforce_applies": enforce_on,
        "canonical_loader_session_id": can_id,
        "runtime_session_id": rt_id,
        "runtime_source": rt_source,
        "legacy_selected_session": legacy_id,
        "PILOT_SHADOW_RESULT": shadow_metric,
        "shadow": cmp.as_safe_dict(),
        "PILOT_RECONNECT_PASS": reconnect["ok"] and reconnect["identity_match"],
        "reconnect": reconnect,
        "dispatch_readiness": dispatch_ok,
        "session_count": session_count,
        "active_count": active_count,
        "active_id": active_id,
        "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
        "MessageAttempt_count": msg,
        "challenge_count": ch_count,
        "batch_a_actives": batch_a,
        "a12": a12,
        "a79": a79,
        "queue27": q,
        "coverage27": cov,
        "canonical_mode": mode,
        "canonical_allowlist": allow,
        "process_workers": ids,
        "process_discovery": {
            "mode": meta.get("mode"),
            "cohort": meta.get("cohort_ids"),
            "pin": meta.get("pinned_ids"),
        },
        "MESSAGE_SENT": False,
        "ok": enforce_pass,
    }


if __name__ == "__main__":
    art = asyncio.run(main())
    OUT.write_text(json.dumps(art, indent=2, default=str), encoding="utf-8")
    print(json.dumps(art, indent=2, default=str))
    raise SystemExit(0 if art.get("ok") else 2)
