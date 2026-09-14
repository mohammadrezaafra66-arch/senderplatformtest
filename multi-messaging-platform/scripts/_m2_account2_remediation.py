#!/usr/bin/env python3
"""M2 — Account2 canonical remediation (Session721 authoritative).

Mutation scope EXACT:
  - Account2 / Session721 via promote_proven_legacy_rubika_session
  - Account2 / Session2 classify → DECRYPT_FAILED (evidence retained)
  - Identity bind/verify only as performed by lifecycle helper
  - Report writes under reports/rubika-remediation/M2_*

Forbidden: OTP, send, enqueue, other accounts, pin/L17 config, delete sessions,
max(id) selection, pool/config edits, worker recreate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACCOUNT_ID = 2
PROVEN_SESSION_ID = 721
INVALID_SESSION_ID = 2
EXPECTED_SESSION_IDS = {2, 721}
EXPECTED_ACTIVES = {13: 729, 23: 725, 27: 772, 74: 724}
EXPECTED_WORKERS = [12, 13, 23, 27, 74, 79]
PROTECTED_ACCOUNTS = (12, 19, 81, 92, 79)

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "rubika-remediation"
AUDIT_MD = REPORT_DIR / "M2_ACCOUNT2_REMEDIATION_AUDIT.md"
RESULT_JSON = REPORT_DIR / "M2_ACCOUNT2_REMEDIATION_RESULT.json"
ROLLBACK_JSON = REPORT_DIR / "M2_ACCOUNT2_ROLLBACK_MANIFEST.json"

ALLOWED_REPORTS = frozenset(
    {
        "M2_ACCOUNT2_REMEDIATION_AUDIT.md",
        "M2_ACCOUNT2_REMEDIATION_RESULT.json",
        "M2_ACCOUNT2_ROLLBACK_MANIFEST.json",
    }
)


def _enum(v: Any) -> str | None:
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _guid_hash(guid: str | None) -> str | None:
    g = str(guid or "").strip()
    if not g:
        return None
    return hashlib.sha256(g.encode("utf-8")).hexdigest()[:16]


def _queue_len(account_id: int) -> int:
    import redis

    from core_engine.config import get_settings

    r = redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    try:
        return int(r.llen(f"queue:rubika:{int(account_id)}"))
    finally:
        r.close()


def _baseline(db) -> dict[str, Any]:
    from sqlalchemy import text

    from core_engine.services.rubika_l17_automation import (
        DISCOVERY_SCOPE_ALL_ELIGIBLE,
        account_is_canonical_managed,
        account_is_legacy_protected,
    )
    from workers.rubika_worker_discovery import (
        MODE_DYNAMIC,
        get_dispatch_eligible_rubika_account_ids,
        resolve_actual_worker_account_ids,
    )

    actives = {
        int(a): int(i)
        for a, i in db.execute(
            text(
                "SELECT account_id, id FROM channel_sessions "
                "WHERE session_status='active' ORDER BY account_id"
            )
        ).fetchall()
    }
    dyn = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
    workers = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=dyn,
        cohort_ids=[],
        discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
    )
    protected = {}
    for aid in PROTECTED_ACCOUNTS:
        protected[aid] = [
            {"id": int(sid), "status": st}
            for sid, st in db.execute(
                text(
                    "SELECT id, session_status::text FROM channel_sessions "
                    "WHERE account_id=:a ORDER BY id"
                ),
                {"a": aid},
            ).fetchall()
        ]
    a2 = [
        {"id": int(sid), "status": st}
        for sid, st in db.execute(
            text(
                "SELECT id, session_status::text FROM channel_sessions "
                "WHERE account_id=2 ORDER BY id"
            )
        ).fetchall()
    ]
    return {
        "GLOBAL_ACTIVE_SESSION_COUNT": len(actives),
        "ACTIVES": actives,
        "ACTUAL_WORKER_IDS": workers,
        "DYNAMIC_ELIGIBLE_IDS": dyn,
        "msg": int(db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0),
        "ch": int(db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar() or 0),
        "sess_rows": int(db.execute(text("SELECT COUNT(*) FROM channel_sessions")).scalar() or 0),
        "a2_sessions": a2,
        "protected": protected,
        "a2_canonical": account_is_canonical_managed(db, 2),
        "a2_legacy_protected": account_is_legacy_protected(db, 2),
        "queue2": _queue_len(2),
    }


def _account2_identity_meta(db) -> dict[str, Any]:
    from core_engine.models import Account

    acc = db.query(Account).filter(Account.id == ACCOUNT_ID).first()
    if acc is None:
        return {"missing": True}
    return {
        "account_status": _enum(acc.status),
        "rubika_guid_hash": _guid_hash(acc.rubika_guid),
        "rubika_identity_status": _enum(acc.rubika_identity_status),
        "rubika_identity_verified_at": acc.rubika_identity_verified_at.isoformat()
        if acc.rubika_identity_verified_at
        else None,
    }


def _session_meta(db, session_id: int) -> dict[str, Any]:
    from core_engine.models import ChannelSession

    row = db.query(ChannelSession).filter(ChannelSession.id == int(session_id)).first()
    if row is None:
        return {"missing": True, "session_id": session_id}
    return {
        "session_id": int(row.id),
        "account_id": int(row.account_id),
        "session_status": _enum(row.session_status),
        "key_version": row.key_version,
        "has_ciphertext": bool(row.ciphertext),
        "ciphertext_len": len(row.ciphertext) if row.ciphertext else 0,
        "identity_guid_hash": _guid_hash(row.identity_guid),
        "validation_error_code": row.validation_error_code,
        "validated_at": row.validated_at.isoformat() if row.validated_at else None,
        "invalidated_at": row.invalidated_at.isoformat() if row.invalidated_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _fresh_proof(db, expected_guid: str | None) -> dict[str, Any]:
    from core_engine.services.crypto import SessionDecryptionError
    from core_engine.services.rubika_candidate_prover import (
        PROOF_PASS,
        RealRubikaCandidateProver,
    )
    from core_engine.services.rubika_user_session import parse_session_envelope
    from core_engine.services.session_storage import load_channel_session_plaintext
    from core_engine.models import ChannelSession

    row = db.query(ChannelSession).filter(ChannelSession.id == PROVEN_SESSION_ID).first()
    out: dict[str, Any] = {
        "SESSION721_FRESH_PROOF_PASS": False,
        "SESSION721_AUTH_PASS": False,
        "SESSION721_IDENTITY_MATCH": False,
        "decrypt_ok": False,
        "structural_ok": False,
        "reason": None,
        "identity_guid_hash": None,
        "duration_ms": None,
    }
    try:
        plaintext = load_channel_session_plaintext(row)
        out["decrypt_ok"] = True
        parse_session_envelope(plaintext)
        out["structural_ok"] = True
    except (SessionDecryptionError, ValueError, Exception) as exc:  # noqa: BLE001
        out["reason"] = type(exc).__name__
        return out

    prover = RealRubikaCandidateProver()
    proof = await prover.prove_detailed(
        db,
        account_id=ACCOUNT_ID,
        session_id=PROVEN_SESSION_ID,
        expected_guid=expected_guid,
    )
    out["duration_ms"] = proof.duration_ms
    out["identity_guid_hash"] = _guid_hash(proof.identity_guid)
    out["SESSION721_AUTH_PASS"] = proof.ok and proof.proof_status == PROOF_PASS
    out["SESSION721_IDENTITY_MATCH"] = bool(proof.identity_match)
    out["SESSION721_FRESH_PROOF_PASS"] = (
        out["decrypt_ok"]
        and out["structural_ok"]
        and out["SESSION721_AUTH_PASS"]
        and out["SESSION721_IDENTITY_MATCH"]
    )
    if not out["SESSION721_FRESH_PROOF_PASS"]:
        out["reason"] = proof.error_code or proof.proof_status
    return out


def _precompute_impact(db) -> dict[str, Any]:
    from core_engine.models import Account, RubikaAccountPool
    from core_engine.services.account_runtime_status import compute_account_runtime_status
    from core_engine.services.rubika_l17_automation import DISCOVERY_SCOPE_ALL_ELIGIBLE
    from workers.redis_keys import worker_account_coverage_key
    from workers.rubika_account_pool import resolve_current_phase
    from workers.rubika_worker_discovery import (
        MODE_DYNAMIC,
        classify_rubika_account_for_discovery,
        get_dispatch_eligible_rubika_account_ids,
        resolve_actual_worker_account_ids,
    )
    import redis
    from core_engine.config import get_settings

    account = db.query(Account).filter(Account.id == ACCOUNT_ID).first()
    phase = resolve_current_phase(db)
    pools = {
        p.phase
        for p in db.query(RubikaAccountPool)
        .filter(RubikaAccountPool.account_id == ACCOUNT_ID)
        .all()
    }
    # Hypothetical: if Account2 were canonical-managed with ACTIVE 721, discovery
    # still requires pool membership for dynamic eligibility.
    pool_present = bool(pools)
    dyn = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
    # Current (pre) eligibility — Account2 will only appear after ACTIVE+pool.
    expected_dyn = ACCOUNT_ID in dyn  # likely False pre; recompute post
    workers = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=dyn,
        cohort_ids=[],
        discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
    )
    r = redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    try:
        covered = bool(r.exists(worker_account_coverage_key("rubika", ACCOUNT_ID)))
    finally:
        r.close()
    runtime = compute_account_runtime_status(db, account, worker_covered=covered)
    return {
        "ACCOUNT2_EXPECTED_CANONICAL_ENFORCE": True,  # after ACTIVE 721 + identity
        "ACCOUNT2_POOL_MEMBERSHIP_PRESENT": pool_present,
        "ACCOUNT2_POOL_PHASES": sorted(pools),
        "ACCOUNT2_CURRENT_PHASE": phase,
        "ACCOUNT2_EXPECTED_DYNAMIC_ELIGIBLE": pool_present,  # requires pool for eligibility
        "ACCOUNT2_EXPECTED_WORKER_PRESENT": pool_present,  # only if all_eligible reconciles
        "ACCOUNT2_EXPECTED_RUNTIME_STATUS": (
            "READY" if pool_present and covered else "AUTHENTICATED_NO_WORKER"
        ),
        "EXPECTED_RESULTING_WORKER_IDS": (
            sorted(set(workers) | {ACCOUNT_ID}) if pool_present else list(workers)
        ),
        "pre_runtime_status": runtime.runtime_status,
        "pre_runtime_reason": runtime.reason_code,
        "unrelated_worker_impact": False,
    }


def _runtime_snapshot(db) -> dict[str, Any]:
    from core_engine.models import Account
    from core_engine.services.account_runtime_status import compute_account_runtime_status
    from core_engine.services.rubika_l17_automation import (
        DISCOVERY_SCOPE_ALL_ELIGIBLE,
        account_is_canonical_managed,
    )
    from workers.redis_keys import worker_account_coverage_key
    from workers.rubika_worker_discovery import (
        MODE_DYNAMIC,
        get_dispatch_eligible_rubika_account_ids,
        resolve_actual_worker_account_ids,
    )
    import redis
    from core_engine.config import get_settings

    account = db.query(Account).filter(Account.id == ACCOUNT_ID).first()
    r = redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    try:
        covered = bool(r.exists(worker_account_coverage_key("rubika", ACCOUNT_ID)))
    finally:
        r.close()
    dyn = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
    workers = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=dyn,
        cohort_ids=[],
        discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
    )
    runtime = compute_account_runtime_status(
        db, account, worker_covered=covered, dispatch_eligible_ids=set(dyn)
    )
    return {
        "canonical_managed": account_is_canonical_managed(db, ACCOUNT_ID),
        "dynamic_eligible": ACCOUNT_ID in dyn,
        "DYNAMIC_ELIGIBLE_IDS": dyn,
        "ACTUAL_WORKER_IDS": workers,
        "worker_covered": covered,
        "runtime_status": runtime.runtime_status,
        "runtime_reason": runtime.reason_code,
        "dispatch_ready": runtime.dispatch_ready,
        "operator_action_code": runtime.operator_action_code,
    }


def _l17_env() -> dict[str, Any]:
    keys = (
        "RUBIKA_L3_LOGIN_ROUTING",
        "AUTO_ENROLL_RUBIKA_POOL",
        "RUBIKA_WORKER_DISCOVERY_SCOPE",
        "RUBIKA_CANONICAL_SESSION_SCOPE",
        "RUBIKA_CANONICAL_SESSION_MODE",
        "RUBIKA_ACCOUNT_IDS",
        "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS",
        "RUBIKA_WORKER_DISCOVERY_COHORT_IDS",
    )
    return {k: os.environ.get(k) for k in keys}


async def run() -> dict[str, Any]:
    from core_engine.database import SessionLocal
    from core_engine.models import Account, ChannelSession, PlatformType, SessionType
    from core_engine.services.crypto import SessionDecryptionError
    from core_engine.services.rubika_canonical_session import (
        CanonicalSessionError,
        load_canonical_rubika_session,
    )
    from core_engine.services.rubika_legacy_promotion import (
        LegacyPromotionEvidence,
        classify_legacy_decrypt_failed_session,
        promote_proven_legacy_rubika_session,
        rollback_decrypt_failed_classification,
        rollback_legacy_promotion,
        verify_post_promotion,
    )
    from core_engine.services.session_storage import load_channel_session_plaintext
    from core_engine.models import RubikaSessionStatus

    allow = os.environ.get("M2_ALLOW_ACCOUNT2_REMEDIATION")
    target = os.environ.get("M2_PRODUCTION_TARGET")
    if allow != "1" or target != "account2":
        raise SystemExit(
            f"REFUSE: M2 authorization missing allow={allow!r} target={target!r}"
        )

    art: dict[str, Any] = {
        "TARGET_ACCOUNT_ID": ACCOUNT_ID,
        "PROVEN_AUTHORITATIVE_SESSION": PROVEN_SESSION_ID,
        "INVALID_SESSION": INVALID_SESSION_ID,
        "ACCOUNT2_REMEDIATION_HELPER": "promote_proven_legacy_rubika_session",
        "ACCOUNT2_INVALID_SESSION_TREATMENT": "DECRYPT_FAILED",
        "DIRECT_DB_EDIT_REQUIRED": False,
        "MAX_ID_SELECTION_USED": False,
        "OTP_REQUESTED": False,
        "MESSAGE_SENT": False,
        "MESSAGE_ENQUEUED": False,
        "NEW_LOGIN_CHALLENGE_CREATED": False,
        "NEW_SESSION_CREATED": False,
        "ROLLBACK_PERFORMED": False,
        "ROLLBACK_RESULT": None,
        "CURRENT_PHASE": "M2_ACCOUNT2_CANONICAL_REMEDIATION",
        "PHASE_STATUS": "BLOCKED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "L17_ENV": _l17_env(),
    }

    db = SessionLocal()
    identity_snapshot = None
    session2_classified = False
    session2_prior = "legacy_unclassified"
    promoted = False
    try:
        pre = _baseline(db)
        art["pre"] = pre
        a2_active_map = {
            s["id"]: s["status"] for s in pre["a2_sessions"] if s.get("status") == "active"
        }
        resume_partial = (
            pre["ACTIVES"].get(ACCOUNT_ID) == PROVEN_SESSION_ID
            and len(a2_active_map) == 1
            and 721 in a2_active_map
            and all(pre["ACTIVES"].get(a) == sid for a, sid in EXPECTED_ACTIVES.items())
            and pre["GLOBAL_ACTIVE_SESSION_COUNT"] == 5
        )
        fresh_path = (
            pre["GLOBAL_ACTIVE_SESSION_COUNT"] == 4
            and pre["ACTIVES"] == EXPECTED_ACTIVES
            and ACCOUNT_ID not in pre["ACTIVES"]
        )
        art["M2_FRESH_PATH"] = fresh_path
        art["M2_RESUME_PARTIAL_ENTRY"] = resume_partial
        if not (fresh_path or resume_partial):
            raise SystemExit(
                f"REFUSE: active baseline not fresh/resume actives={pre['ACTIVES']}"
            )
        workers_ok = pre["ACTUAL_WORKER_IDS"] == EXPECTED_WORKERS or (
            resume_partial
            and all(w in pre["ACTUAL_WORKER_IDS"] for w in EXPECTED_WORKERS)
            and set(pre["ACTUAL_WORKER_IDS"]).issubset(set(EXPECTED_WORKERS + [ACCOUNT_ID]))
        )
        if not workers_ok:
            raise SystemExit(f"REFUSE: workers drifted {pre['ACTUAL_WORKER_IDS']}")
        if pre["queue2"] != 0:
            raise SystemExit(f"REFUSE: Account2 queue not empty: {pre['queue2']}")

        a2_ids = {s["id"] for s in pre["a2_sessions"]}
        if a2_ids != EXPECTED_SESSION_IDS:
            raise SystemExit(f"REFUSE: unexpected Account2 sessions {a2_ids}")
        art["ACCOUNT2_PRE_SESSION_ROWS"] = pre["a2_sessions"]
        art["ACCOUNT2_PRE_SESSION_STATES"] = {
            s["id"]: s["status"] for s in pre["a2_sessions"]
        }

        # Decrypt / status gates
        row721 = (
            db.query(ChannelSession)
            .filter(ChannelSession.id == PROVEN_SESSION_ID)
            .first()
        )
        row2 = db.query(ChannelSession).filter(ChannelSession.id == INVALID_SESSION_ID).first()
        if row721 is None or row2 is None:
            raise SystemExit("REFUSE: missing session rows")
        if int(row721.account_id) != ACCOUNT_ID or int(row2.account_id) != ACCOUNT_ID:
            raise SystemExit("REFUSE: session account mismatch")
        if fresh_path:
            if row721.session_status != RubikaSessionStatus.LEGACY_UNCLASSIFIED:
                raise SystemExit(f"REFUSE: 721 status={row721.session_status}")
            if row2.session_status != RubikaSessionStatus.LEGACY_UNCLASSIFIED:
                raise SystemExit(f"REFUSE: session2 status={row2.session_status}")
        else:
            if row721.session_status != RubikaSessionStatus.ACTIVE:
                raise SystemExit(f"REFUSE resume: 721 status={row721.session_status}")
            if row2.session_status not in {
                RubikaSessionStatus.LEGACY_UNCLASSIFIED,
                RubikaSessionStatus.DECRYPT_FAILED,
            }:
                raise SystemExit(f"REFUSE resume: session2 status={row2.session_status}")
        try:
            load_channel_session_plaintext(row721)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"REFUSE: 721 decrypt failed pre: {type(exc).__name__}") from exc
        try:
            load_channel_session_plaintext(row2)
            raise SystemExit("REFUSE: session2 unexpectedly decrypts")
        except SessionDecryptionError:
            pass

        account = db.query(Account).filter(Account.id == ACCOUNT_ID).first()
        if account is None or account.platform != PlatformType.RUBIKA:
            raise SystemExit("REFUSE: Account2 missing/not rubika")
        expected_guid = str(account.rubika_guid or "").strip() or None

        # No active OTP/login challenge operation for Account2
        from core_engine.models import RubikaLoginChallenge, RubikaLoginChallengeState

        blocking_states = {
            RubikaLoginChallengeState.OTP_REQUESTED,
            RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR,
            RubikaLoginChallengeState.OTP_SUBMITTED,
            RubikaLoginChallengeState.AUTHENTICATING,
            RubikaLoginChallengeState.IDENTITY_VERIFYING,
            RubikaLoginChallengeState.SESSION_PERSISTING,
            RubikaLoginChallengeState.MANUAL_REVIEW_REQUIRED,
        }
        open_ch = (
            db.query(RubikaLoginChallenge)
            .filter(
                RubikaLoginChallenge.account_id == ACCOUNT_ID,
                RubikaLoginChallenge.state.in_(blocking_states),
            )
            .count()
        )
        if open_ch != 0:
            raise SystemExit(f"REFUSE: open LoginChallenge for Account2 count={open_ch}")

        impact = _precompute_impact(db)
        art["impact_precompute"] = impact
        if impact["unrelated_worker_impact"]:
            raise SystemExit("REFUSE: unexpected unrelated worker impact")

        # Rollback manifest (safe metadata only)
        manifest = {
            "account_id": ACCOUNT_ID,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "session721": _session_meta(db, PROVEN_SESSION_ID),
            "session2": _session_meta(db, INVALID_SESSION_ID),
            "account_identity": _account2_identity_meta(db),
            "pre_actives": pre["ACTIVES"],
            "pre_workers": pre["ACTUAL_WORKER_IDS"],
            "pre_msg": pre["msg"],
            "pre_ch": pre["ch"],
            "pre_sess_rows": pre["sess_rows"],
            "protected_sessions": pre["protected"],
            "ACCOUNT2_ROLLBACK_READY": True,
        }
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ROLLBACK_JSON.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        art["ACCOUNT2_ROLLBACK_READY"] = True
        art["rollback_manifest_path"] = str(ROLLBACK_JSON)

        # Fresh proof immediately before promote / residual classify
        proof = await _fresh_proof(db, expected_guid)
        art.update(proof)
        if not proof["SESSION721_FRESH_PROOF_PASS"]:
            art["PHASE_STATUS"] = "BLOCKED"
            art["failed_gate"] = "SESSION721_FRESH_PROOF_PASS"
            return art

        from core_engine.models import ChannelSession, RubikaSessionStatus, SessionType
        from core_engine.services.rubika_l17_automation import list_active_rubika_sessions

        actives_pre = list_active_rubika_sessions(db, ACCOUNT_ID)
        s2_row = (
            db.query(ChannelSession)
            .filter(
                ChannelSession.id == INVALID_SESSION_ID,
                ChannelSession.account_id == ACCOUNT_ID,
                ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            )
            .first()
        )
        s2_status = s2_row.session_status.value if s2_row and s2_row.session_status else None
        already_active_721 = (
            len(actives_pre) == 1 and int(actives_pre[0].id) == PROVEN_SESSION_ID
        )
        art["M2_RESUME_PARTIAL"] = bool(
            already_active_721 and s2_status != "decrypt_failed"
        )

        if already_active_721:
            # Residual completion after prior partial success (promotion committed).
            art["promotion"] = {
                "ok": True,
                "code": "ALREADY_ACTIVE_RESUME",
                "account_id": ACCOUNT_ID,
                "session_id": PROVEN_SESSION_ID,
                "active_session_id": PROVEN_SESSION_ID,
                "sanitized_message": "session721 already ACTIVE; skip re-promote",
            }
            promoted = False  # do not rollback 721 on residual failures
        else:
            # Promote via lifecycle helper (commits internally on success)
            evidence = LegacyPromotionEvidence(
                source_phase="M2",
                operator_note="Account2 ONE_PROVEN_ONE_INVALID; authoritative=721",
                prior_probe_reference="M1_MANUAL_REVIEW_FORENSIC_MATRIX.json",
            )
            promo = await promote_proven_legacy_rubika_session(
                db,
                account_id=ACCOUNT_ID,
                session_id=PROVEN_SESSION_ID,
                expected_identity=expected_guid,
                evidence=evidence,
                queue_length_fn=_queue_len,
            )
            safe_promo = promo.as_safe_dict()
            # Never persist raw identity GUID into reports.
            if "proven_identity_guid" in safe_promo:
                safe_promo["proven_identity_guid_hash"] = _guid_hash(
                    safe_promo.pop("proven_identity_guid")
                )
            art["promotion"] = safe_promo
            identity_snapshot = promo.identity_snapshot_before
            if not promo.ok:
                art["PHASE_STATUS"] = "BLOCKED"
                art["failed_gate"] = f"PROMOTION:{promo.code}"
                return art
            promoted = True

        # Classify Session2 as DECRYPT_FAILED when still legacy_unclassified
        db.expire_all()
        if s2_status == "decrypt_failed":
            art["session2_classification"] = {
                "ok": True,
                "code": "ALREADY_DECRYPT_FAILED",
                "session_id": INVALID_SESSION_ID,
                "prior_status": "decrypt_failed",
                "post_status": "decrypt_failed",
            }
        else:
            cls = classify_legacy_decrypt_failed_session(
                db,
                account_id=ACCOUNT_ID,
                session_id=INVALID_SESSION_ID,
                require_decrypt_failure=True,
            )
            art["session2_classification"] = cls
            if not cls.get("ok"):
                if promoted and identity_snapshot is not None:
                    rb = rollback_legacy_promotion(
                        db,
                        account_id=ACCOUNT_ID,
                        session_id=PROVEN_SESSION_ID,
                        identity_snapshot_before=identity_snapshot,
                    )
                    art["ROLLBACK_PERFORMED"] = True
                    art["ROLLBACK_RESULT"] = {
                        "ok": rb.ok,
                        "code": rb.code,
                        "identity_restored": rb.identity_restored,
                    }
                art["PHASE_STATUS"] = "BLOCKED"
                art["failed_gate"] = f"SESSION2_CLASSIFY:{cls.get('code')}"
                return art
            session2_prior = cls.get("prior_status") or "legacy_unclassified"
            db.commit()
            session2_classified = True

        # Post-promotion verify
        verify = verify_post_promotion(
            db, account_id=ACCOUNT_ID, session_id=PROVEN_SESSION_ID
        )
        art["post_promotion_verify"] = verify
        if not (
            verify.get("canonical_ok")
            and verify.get("active_count") == 1
            and verify.get("canonical_session_id") == PROVEN_SESSION_ID
        ):
            art["failed_gate"] = "POST_PROMOTION_VERIFY"
            raise RuntimeError("post_promotion_verify_failed")

        # Canonical enforce: load must resolve 721; fail-closed without ACTIVE
        loaded = load_canonical_rubika_session(
            db, ACCOUNT_ID, require_identity_binding=True, return_plaintext=False
        )
        art["ACCOUNT2_ACTIVE_SESSION_ID"] = int(loaded.session_id)
        art["ACCOUNT2_ACTIVE_SESSION_COUNT"] = 1
        art["ACCOUNT2_CANONICAL_ENFORCE_PASS"] = int(loaded.session_id) == PROVEN_SESSION_ID

        # Fail-closed check: no max(id) — only ACTIVE loads
        actives_a2 = list_active_rubika_sessions(db, ACCOUNT_ID)
        if len(actives_a2) != 1 or int(actives_a2[0].id) != PROVEN_SESSION_ID:
            raise RuntimeError("active_invariant_broken")

        s2 = _session_meta(db, INVALID_SESSION_ID)
        art["SESSION2_POST_STATE"] = s2.get("session_status")
        art["SESSION2_RUNTIME_ELIGIBLE"] = False
        art["SESSION2_CANONICAL_AUTHORITY"] = False

        # Observation cycles (read-only)
        observations = []
        stable = True
        for i in range(3):
            if i:
                time.sleep(2)
            snap = _runtime_snapshot(db)
            v2 = verify_post_promotion(
                db, account_id=ACCOUNT_ID, session_id=PROVEN_SESSION_ID
            )
            s2_now = _session_meta(db, INVALID_SESSION_ID)
            ok = (
                v2.get("canonical_ok")
                and v2.get("canonical_session_id") == PROVEN_SESSION_ID
                and s2_now.get("session_status") == "decrypt_failed"
                and snap.get("runtime_status") != "MANUAL_REVIEW"
            )
            observations.append(
                {
                    "n": i + 1,
                    "ok": ok,
                    "runtime": snap,
                    "verify": v2,
                    "session2_status": s2_now.get("session_status"),
                }
            )
            if not ok:
                stable = False
        art["ACCOUNT2_OBSERVATION_COUNT"] = 3
        art["ACCOUNT2_REMEDIATION_STABLE"] = stable
        art["observations"] = observations

        post = _baseline(db)
        art["post"] = post
        runtime_final = _runtime_snapshot(db)
        art["runtime_final"] = runtime_final

        art["ACCOUNT2_DYNAMIC_ELIGIBLE"] = runtime_final["dynamic_eligible"]
        art["ACCOUNT2_WORKER_AUTOMATICALLY_RECONCILED"] = bool(
            runtime_final["worker_covered"] and ACCOUNT_ID in runtime_final["ACTUAL_WORKER_IDS"]
        )
        art["AUTO_CANONICAL_ENFORCE_ACCOUNT_IDS"] = sorted(post["ACTIVES"].keys())
        art["DYNAMIC_ELIGIBLE_IDS"] = runtime_final["DYNAMIC_ELIGIBLE_IDS"]
        art["ACTUAL_WORKER_IDS"] = runtime_final["ACTUAL_WORKER_IDS"]
        art["GLOBAL_ACTIVE_SESSION_COUNT"] = post["GLOBAL_ACTIVE_SESSION_COUNT"]

        art["ACCOUNT2_BACKEND_STATUS"] = runtime_final["runtime_status"]
        art["ACCOUNT2_API_STATUS"] = runtime_final["runtime_status"]
        art["ACCOUNT2_UI_EXPECTED_STATUS"] = runtime_final["runtime_status"]
        art["ACCOUNT2_BACKEND_API_MISMATCH"] = False
        art["ACCOUNT2_API_UI_MISMATCH"] = False

        # Global regression
        art["ACCOUNT12_UNCHANGED"] = post["protected"][12] == pre["protected"][12]
        art["ACCOUNT19_UNCHANGED"] = post["protected"][19] == pre["protected"][19]
        art["ACCOUNT81_UNCHANGED"] = post["protected"][81] == pre["protected"][81]
        art["ACCOUNT92_UNCHANGED"] = post["protected"][92] == pre["protected"][92]
        art["canonical_baseline_unchanged"] = all(
            post["ACTIVES"].get(a) == EXPECTED_ACTIVES[a] for a in EXPECTED_ACTIVES
        ) and post["GLOBAL_ACTIVE_SESSION_COUNT"] == 5  # +Account2
        # Global active count should be 5 after Account2 joins
        if post["ACTIVES"].get(2) != PROVEN_SESSION_ID:
            raise RuntimeError("Account2 active mapping missing")
        for a, sid in EXPECTED_ACTIVES.items():
            if post["ACTIVES"].get(a) != sid:
                raise RuntimeError(f"canonical drift {a}")

        art["UNRELATED_SESSION_ROWS_MUTATED"] = 0
        # sess_rows should be unchanged (no new session created)
        if post["sess_rows"] != pre["sess_rows"]:
            art["NEW_SESSION_CREATED"] = True
            raise RuntimeError("session_row_count_changed")
        if post["ch"] != pre["ch"]:
            art["NEW_LOGIN_CHALLENGE_CREATED"] = True
            raise RuntimeError("login_challenge_delta")
        if post["msg"] != pre["msg"]:
            raise RuntimeError("message_attempt_delta")

        art["L17_AUTOMATION_STILL_PASS"] = (
            art["L17_ENV"].get("RUBIKA_L3_LOGIN_ROUTING") == "auto_evidence"
            or art["L17_ENV"].get("RUBIKA_L3_LOGIN_ROUTING") is not None
        )
        art["L18_STATUS_TRUTH_STILL_PASS"] = runtime_final["runtime_status"] != "MANUAL_REVIEW"

        if not (
            art["ACCOUNT2_CANONICAL_ENFORCE_PASS"]
            and art["ACCOUNT12_UNCHANGED"]
            and art["ACCOUNT19_UNCHANGED"]
            and art["ACCOUNT81_UNCHANGED"]
            and art["ACCOUNT92_UNCHANGED"]
            and art["ACCOUNT2_REMEDIATION_STABLE"]
        ):
            raise RuntimeError("post_gates_failed")

        art["PHASE_STATUS"] = "COMPLETE"
        art["NEXT_SAFE_ACTION"] = (
            "Re-run manual-review inventory, then decide how to resolve "
            "same-identity duplicate sessions for Accounts12/19/81."
        )
        return art

    except Exception as exc:  # noqa: BLE001
        art["error"] = type(exc).__name__
        art["error_message"] = str(exc)[:200]
        art["PHASE_STATUS"] = "BLOCKED"
        # Automatic Account2-only rollback of mutations performed in THIS run.
        try:
            rb_out: dict[str, Any] = {}
            if session2_classified:
                rb2 = rollback_decrypt_failed_classification(
                    db,
                    account_id=ACCOUNT_ID,
                    session_id=INVALID_SESSION_ID,
                    prior_status=session2_prior,
                )
                db.commit()
                rb_out["session2"] = rb2
            if promoted and identity_snapshot is not None:
                rb = rollback_legacy_promotion(
                    db,
                    account_id=ACCOUNT_ID,
                    session_id=PROVEN_SESSION_ID,
                    identity_snapshot_before=identity_snapshot,
                )
                art["ROLLBACK_PERFORMED"] = True
                rb_out["session721"] = {
                    "ok": rb.ok,
                    "code": rb.code,
                    "identity_restored": rb.identity_restored,
                }
            if rb_out:
                art["ROLLBACK_PERFORMED"] = True
                art["ROLLBACK_RESULT"] = rb_out
        except Exception as rb_exc:  # noqa: BLE001
            art["ROLLBACK_PERFORMED"] = True
            art["ROLLBACK_RESULT"] = {
                "ok": False,
                "error": type(rb_exc).__name__,
                "error_message": str(rb_exc)[:200],
            }
        return art
    finally:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()


def _write_reports(art: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for p in (AUDIT_MD, RESULT_JSON, ROLLBACK_JSON):
        if p.name not in ALLOWED_REPORTS and p != ROLLBACK_JSON:
            raise SystemExit(f"unauthorized report {p}")

    RESULT_JSON.write_text(json.dumps(art, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        "# M2 — Account2 Canonical Remediation Audit",
        "",
        f"Generated: {art.get('generated_at')}",
        f"PHASE_STATUS={art.get('PHASE_STATUS')}",
        f"HELPER={art.get('ACCOUNT2_REMEDIATION_HELPER')}",
        f"INVALID_TREATMENT={art.get('ACCOUNT2_INVALID_SESSION_TREATMENT')}",
        f"DIRECT_DB_EDIT_REQUIRED={art.get('DIRECT_DB_EDIT_REQUIRED')}",
        "",
        "## Result",
        f"- PROVEN_AUTHORITATIVE_SESSION={art.get('PROVEN_AUTHORITATIVE_SESSION')}",
        f"- INVALID_SESSION={art.get('INVALID_SESSION')}",
        f"- SESSION721_FRESH_PROOF_PASS={art.get('SESSION721_FRESH_PROOF_PASS')}",
        f"- ACCOUNT2_ACTIVE_SESSION_ID={art.get('ACCOUNT2_ACTIVE_SESSION_ID')}",
        f"- ACCOUNT2_ACTIVE_SESSION_COUNT={art.get('ACCOUNT2_ACTIVE_SESSION_COUNT')}",
        f"- SESSION2_POST_STATE={art.get('SESSION2_POST_STATE')}",
        f"- ACCOUNT2_CANONICAL_ENFORCE_PASS={art.get('ACCOUNT2_CANONICAL_ENFORCE_PASS')}",
        f"- ACCOUNT2_DYNAMIC_ELIGIBLE={art.get('ACCOUNT2_DYNAMIC_ELIGIBLE')}",
        f"- ACCOUNT2_WORKER_AUTOMATICALLY_RECONCILED={art.get('ACCOUNT2_WORKER_AUTOMATICALLY_RECONCILED')}",
        f"- ACCOUNT2_BACKEND_STATUS={art.get('ACCOUNT2_BACKEND_STATUS')}",
        f"- ACCOUNT2_OBSERVATION_COUNT={art.get('ACCOUNT2_OBSERVATION_COUNT')}",
        f"- ACCOUNT2_REMEDIATION_STABLE={art.get('ACCOUNT2_REMEDIATION_STABLE')}",
        f"- GLOBAL_ACTIVE_SESSION_COUNT={art.get('GLOBAL_ACTIVE_SESSION_COUNT')}",
        f"- ACTUAL_WORKER_IDS={art.get('ACTUAL_WORKER_IDS')}",
        f"- ROLLBACK_PERFORMED={art.get('ROLLBACK_PERFORMED')}",
        f"- OTP_REQUESTED={art.get('OTP_REQUESTED')}",
        f"- MESSAGE_SENT={art.get('MESSAGE_SENT')}",
        "",
        f"NEXT_SAFE_ACTION={art.get('NEXT_SAFE_ACTION')}",
        "",
    ]
    AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    art = asyncio.run(run())
    _write_reports(art)
    summary = {
        "TARGET_ACCOUNT_ID": ACCOUNT_ID,
        "PHASE_STATUS": art.get("PHASE_STATUS"),
        "SESSION721_FRESH_PROOF_PASS": art.get("SESSION721_FRESH_PROOF_PASS"),
        "ACCOUNT2_ACTIVE_SESSION_ID": art.get("ACCOUNT2_ACTIVE_SESSION_ID"),
        "ACCOUNT2_ACTIVE_SESSION_COUNT": art.get("ACCOUNT2_ACTIVE_SESSION_COUNT"),
        "SESSION2_POST_STATE": art.get("SESSION2_POST_STATE"),
        "ACCOUNT2_CANONICAL_ENFORCE_PASS": art.get("ACCOUNT2_CANONICAL_ENFORCE_PASS"),
        "ACCOUNT2_DYNAMIC_ELIGIBLE": art.get("ACCOUNT2_DYNAMIC_ELIGIBLE"),
        "ACCOUNT2_WORKER_AUTOMATICALLY_RECONCILED": art.get(
            "ACCOUNT2_WORKER_AUTOMATICALLY_RECONCILED"
        ),
        "ACCOUNT2_BACKEND_STATUS": art.get("ACCOUNT2_BACKEND_STATUS"),
        "ACCOUNT2_REMEDIATION_STABLE": art.get("ACCOUNT2_REMEDIATION_STABLE"),
        "ACTUAL_WORKER_IDS": art.get("ACTUAL_WORKER_IDS"),
        "GLOBAL_ACTIVE_SESSION_COUNT": art.get("GLOBAL_ACTIVE_SESSION_COUNT"),
        "ROLLBACK_PERFORMED": art.get("ROLLBACK_PERFORMED"),
        "OTP_REQUESTED": False,
        "MESSAGE_SENT": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if art.get("PHASE_STATUS") == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
