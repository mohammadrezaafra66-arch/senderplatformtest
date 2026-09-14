#!/usr/bin/env python3
"""L16 Account27 post-login verify: forensic + reconnect + shadow + eligibility.

STRICTLY READ-ONLY against production state:
- SELECT / ORM reads only; SET TRANSACTION READ ONLY; always rollback+close
- Redis: ping / exists / llen only (no writes)
- Live Rubika reconnect probe: no DB/Redis persistence, no OTP, no send
- Report JSON write is host/bind-mount artifact only (no prod mutation)

Forbidden (absent from this script): INSERT/UPDATE/DELETE, commit/flush,
Redis SET/DEL, session create/promote, identity/challenge mutation, OTP,
cohort/allowlist edits, docker recreate, enqueue/send.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

PILOT = 27
SID = 772
BATCH = {13: 729, 23: 725, 74: 724}
# Bind-mounted scripts path so host can collect the artifact.
OUT_SCRIPTS = Path("/app/scripts/_l16_a27_post_login_verify.out.json")
OUT_REPORTS = Path("/app/reports/rubika-remediation/L16_A27_POST_LOGIN_VERIFY.json")

_FORBIDDEN_SRC = re.compile(
    r"\b(INSERT|UPDATE|DELETE|db\.commit|db\.flush|\.commit\(\)|\.flush\(\)|"
    r"r\.set\b|r\.setex\b|r\.hset\b|r\.delete\b|r\.lpush\b|r\.rpush\b|"
    r"request_otp|submit_otp|compose.*up|docker.*recreate)\b",
    re.I,
)


def _self_audit() -> dict:
    src = Path(__file__).read_text(encoding="utf-8")
    # Markers constructed so this auditor's source does not contain them verbatim.
    mark_s = "# === " + "VERIFY_BODY_START" + " ==="
    mark_e = "# === " + "VERIFY_BODY_END" + " ==="
    start = src.find(mark_s)
    end = src.find(mark_e)
    if start < 0 or end < 0 or end <= start:
        return {
            "A27_POST_LOGIN_VERIFY_READ_ONLY": False,
            "A27_POST_LOGIN_VERIFY_SECRET_SAFE": False,
            "forbidden_hits": ["missing_body_markers"],
        }
    body = src[start:end]
    hits = _FORBIDDEN_SRC.findall(body)
    read_only = (
        "SET TRANSACTION READ ONLY" in body
        and "db.rollback()" in body
        and "db.commit" not in body
        and ".commit()" not in body
        and "db.flush" not in body
        and ".flush()" not in body
        and "request_otp" not in body.lower()
        and "submit_otp" not in body.lower()
        and not re.search(r"\b(INSERT|UPDATE|DELETE)\b", body, re.I)
        and not re.search(r"\br\.(set|setex|hset|delete|lpush|rpush)\b", body)
        and "force-recreate" not in body
        and "compose" not in body.lower()
    )
    ret = body[body.find("return {") :] if "return {" in body else body
    secret_safe = (
        "auth_key" not in body.lower()
        and "private_key" not in body.lower()
        and "DATABASE_URL" not in ret
        and "REDIS_URL" not in ret
        and "otp_code" in body
        and not re.search(r'["\']identity_guid["\']\s*:', ret)
        and "session_blob" not in ret.lower()
        and "load_account_session_plaintext" not in ret
        and "envelope" not in ret.lower()
        # OTP_PLAINTEXT_PERSISTED boolean key is intentional (never emits OTP value)
        and "OTP_PLAINTEXT_PERSISTED" in ret
    )
    return {
        "A27_POST_LOGIN_VERIFY_READ_ONLY": read_only,
        "A27_POST_LOGIN_VERIFY_SECRET_SAFE": secret_safe,
        "forbidden_hits": hits,
    }


# === VERIFY_BODY_START ===
async def main() -> dict:
    from sqlalchemy import text

    from core_engine.config import get_settings
    from core_engine.database import SessionLocal
    from core_engine.models import Account, ChannelSession, RubikaLoginChallenge, SessionType
    from core_engine.models import RubikaAccountPool
    from core_engine.services.rubika_canonical_runtime import (
        compare_legacy_vs_canonical,
        select_legacy_rubika_session_row,
    )
    from core_engine.services.rubika_canonical_session import load_canonical_rubika_session
    from core_engine.services.rubika_candidate_prover import RealRubikaCandidateProver
    from workers.config import get_worker_settings
    from workers.redis_keys import worker_account_coverage_key
    from workers.rubika_worker_discovery import (
        classify_rubika_account_for_discovery,
        get_dispatch_eligible_rubika_account_ids,
    )
    from workers.rubika_account_pool import resolve_current_phase
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
        ch = (
            db.query(RubikaLoginChallenge)
            .filter(RubikaLoginChallenge.account_id == PILOT)
            .order_by(RubikaLoginChallenge.created_at.desc())
            .first()
        )
        guid = (acct.rubika_guid or "").strip() if acct else ""
        sess_guid = (active[0].identity_guid or "").strip() if active else ""

        cols = [
            r[0]
            for r in db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='rubika_login_challenges'"
                )
            ).all()
        ]
        otp_col = "otp_code" in cols

        can_row = load_canonical_rubika_session(db, PILOT, require_identity_binding=True)
        can_id = int(can_row.session_id)

        legacy = select_legacy_rubika_session_row(db, PILOT)
        cmp = compare_legacy_vs_canonical(db, PILOT, legacy_row=legacy)

        legacy_id = int(legacy.id) if legacy is not None else None
        shadow = {
            "legacy_selected_session_id": legacy_id,
            "canonical_selected_session_id": can_id,
            "match": legacy_id == can_id == SID,
            "metric": "SHADOW_MATCH" if legacy_id == can_id == SID else "SHADOW_MISMATCH",
            "cmp": cmp.as_safe_dict(),
        }

        proof = await RealRubikaCandidateProver().prove_detailed(
            db,
            account_id=PILOT,
            session_id=SID,
            expected_guid=guid or None,
        )
        reconnect = {
            "ok": bool(proof.ok),
            "code": proof.error_code or proof.proof_status,
            "identity_match": proof.identity_match is True,
            "sanitized_message": proof.sanitized_message,
            # never emit identity_guid / envelope / plaintext
        }

        pt = load_account_session_plaintext(
            db, account_id=PILOT, session_type=SessionType.RUBIKA_SESSION
        )
        dispatch_ok = isinstance(pt, (bytes, bytearray)) and len(pt) > 0
        del pt

        global_active = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        )
        msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
        batch_a = {}
        for aid, _sid in BATCH.items():
            batch_a[str(aid)] = db.execute(
                text(
                    "SELECT id FROM channel_sessions WHERE account_id=:a "
                    "AND session_status::text='active'"
                ),
                {"a": aid},
            ).scalar()

        phase = resolve_current_phase(db)
        pool_phases = {
            r.phase
            for r in db.query(RubikaAccountPool).filter(RubikaAccountPool.account_id == PILOT).all()
        }
        classification = classify_rubika_account_for_discovery(
            db, acct, current_phase=phase, pool_phases=pool_phases
        )
        eligible_ids = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
        pilot_eligible = PILOT in eligible_ids

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
        a3_sess = db.query(ChannelSession).filter(ChannelSession.account_id == 3).count()

        session_count = len(rows)
        active_count = len(active)
        active_id = int(active[0].id) if active else None
        challenge_state = ch.state.value if ch is not None else None
        class_safe = classification.as_safe_dict()
    finally:
        db.rollback()
        db.close()

    s = get_worker_settings()
    ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
    # Authoritative discovery settings live on rubika_worker. core_api often
    # falls back to workers.config defaults (mode=pinned, empty ids).
    worker_env_authoritative = (
        meta.get("mode") == "dynamic"
        and list(meta.get("cohort_ids") or []) == [13, 23, 74]
        and list(meta.get("pinned_ids") or []) == [12, 79]
    )
    r = redis.from_url(s.REDIS_URL, decode_responses=True)
    try:
        await r.ping()
        q = int(await r.llen(f"queue:rubika:{PILOT}"))
        cov = bool(await r.exists(worker_account_coverage_key("rubika", PILOT)))
        cov_ids = {}
        for aid in (12, 13, 23, 74, 79):
            cov_ids[str(aid)] = bool(await r.exists(worker_account_coverage_key("rubika", aid)))
    finally:
        await r.aclose()

    identity_ok = (
        bool(guid)
        and bool(sess_guid)
        and guid == sess_guid
        and reconnect.get("identity_match") is True
        and can_id == SID
    )
    forensic_ok = (
        session_count == 1
        and active_count == 1
        and active_id == SID
        and challenge_state == "ready"
        and not otp_col
        and global_active == 4
        and msg == 5
    )

    if worker_env_authoritative:
        prechecks_pass = (
            ids == [12, 13, 23, 74, 79]
            and str(s.RUBIKA_CANONICAL_SESSION_MODE).lower() == "enforce"
            and str(s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS).replace(" ", "") == "13,23,74"
            and q == 0
        )
    else:
        # Coverage presence proves current workers; settings confirmed on rubika_worker.
        prechecks_pass = (
            str(s.RUBIKA_CANONICAL_SESSION_MODE).lower() == "enforce"
            and str(s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS).replace(" ", "") == "13,23,74"
            and q == 0
            and all(cov_ids[str(a)] for a in (12, 13, 23, 74, 79))
            and not cov
        )

    gates = {
        "worker_env_authoritative": worker_env_authoritative,
        "pre_worker_discovery_mode": meta.get("mode"),
        "pre_worker_cohort": meta.get("cohort_ids"),
        "pre_worker_pin": meta.get("pinned_ids"),
        "pre_actual_workers_from_process": ids,
        "pre_coverage_ids": cov_ids,
        "pre_canonical_mode": s.RUBIKA_CANONICAL_SESSION_MODE,
        "pre_canonical_allowlist": s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS,
        "prechecks_pass": prechecks_pass,
        "worker_settings_probe_required": not worker_env_authoritative,
    }

    return {
        "PILOT_ACCOUNT_ID": PILOT,
        "PILOT_ACTIVE_SESSION_ID": SID,
        "PILOT_SESSION_COUNT": session_count,
        "PILOT_ACTIVE_SESSION_COUNT": active_count,
        "active_session_id_observed": active_id,
        "challenge_final_state": challenge_state,
        "account_guid_bound": bool(guid),
        "session_identity_guid_present": bool(sess_guid),
        "identity_guids_match": bool(guid) and bool(sess_guid) and guid == sess_guid,
        "canonical_loader_session_id": can_id,
        "PILOT_IDENTITY_VERIFY_PASS": identity_ok,
        "PILOT_RECONNECT_PASS": bool(reconnect["ok"] and reconnect["identity_match"]),
        "reconnect": reconnect,
        "OTP_PLAINTEXT_PERSISTED": bool(otp_col),
        "dispatch_readiness": dispatch_ok,
        "PILOT_SHADOW_RESULT": shadow["metric"],
        "legacy_selected_session": legacy_id,
        "canonical_selected_session": can_id,
        "shadow": shadow,
        "PILOT_DYNAMIC_ELIGIBLE": pilot_eligible,
        "eligibility_classification": class_safe,
        "eligible_ids": eligible_ids,
        "forensic_ok": forensic_ok,
        "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
        "MessageAttempt_count": msg,
        "batch_a_actives": batch_a,
        "a12": a12,
        "a79": a79,
        "a3_session_count": a3_sess,
        "queue27": q,
        "coverage27": cov,
        "MESSAGE_SENT": False,
        **gates,
        "ok": forensic_ok
        and identity_ok
        and reconnect["ok"]
        and reconnect["identity_match"]
        and shadow["metric"] == "SHADOW_MATCH"
        and pilot_eligible
        and not otp_col
        and q == 0
        and gates["prechecks_pass"],
    }


# === VERIFY_BODY_END ===


def _write_artifact(art: dict) -> None:
    payload = json.dumps(art, indent=2, default=str)
    OUT_SCRIPTS.write_text(payload, encoding="utf-8")
    try:
        OUT_REPORTS.parent.mkdir(parents=True, exist_ok=True)
        OUT_REPORTS.write_text(payload, encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    audit = _self_audit()
    print(
        json.dumps(
            {
                "A27_POST_LOGIN_VERIFY_READ_ONLY": audit["A27_POST_LOGIN_VERIFY_READ_ONLY"],
                "A27_POST_LOGIN_VERIFY_SECRET_SAFE": audit["A27_POST_LOGIN_VERIFY_SECRET_SAFE"],
            }
        )
    )
    if not (
        audit["A27_POST_LOGIN_VERIFY_READ_ONLY"]
        and audit["A27_POST_LOGIN_VERIFY_SECRET_SAFE"]
    ):
        print(json.dumps(audit, indent=2))
        raise SystemExit(3)

    art = asyncio.run(main())
    art["A27_POST_LOGIN_VERIFY_READ_ONLY"] = True
    art["A27_POST_LOGIN_VERIFY_SECRET_SAFE"] = True
    _write_artifact(art)
    print(json.dumps(art, indent=2, default=str))
    raise SystemExit(0 if art.get("ok") else 2)
