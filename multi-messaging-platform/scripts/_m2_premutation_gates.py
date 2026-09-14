#!/usr/bin/env python3
"""M2 pre-mutation gates — READ-ONLY production proof for Account2.

Never mutates sessions/identity/OTP/queues. Writes only:
  reports/rubika-remediation/M2_PREMUTATION_GATES.json
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACCOUNT_ID = 2
PROVEN_SESSION_ID = 721
INVALID_SESSION_ID = 2
EXPECTED_ACTIVES = {13: 729, 23: 725, 27: 772, 74: 724}
EXPECTED_WORKERS = [12, 13, 23, 27, 74, 79]

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "rubika-remediation"
OUT = REPORT_DIR / "M2_PREMUTATION_GATES.json"
ROLLBACK = REPORT_DIR / "M2_ACCOUNT2_ROLLBACK_MANIFEST.json"


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


async def _fresh_proof(db, expected_guid: str | None) -> dict[str, Any]:
    from core_engine.models import ChannelSession
    from core_engine.services.crypto import SessionDecryptionError
    from core_engine.services.rubika_candidate_prover import (
        PROOF_PASS,
        RealRubikaCandidateProver,
    )
    from core_engine.services.rubika_user_session import parse_session_envelope
    from core_engine.services.session_storage import load_channel_session_plaintext

    row = db.query(ChannelSession).filter(ChannelSession.id == PROVEN_SESSION_ID).first()
    out: dict[str, Any] = {
        "SESSION721_DECRYPT_OK": False,
        "SESSION721_STRUCTURAL_VALID": False,
        "SESSION721_AUTH_PASS": False,
        "SESSION721_IDENTITY_MATCH": False,
        "SESSION721_FRESH_PROOF_PASS": False,
        "identity_guid_hash": None,
        "reason": None,
        "duration_ms": None,
    }
    if row is None or int(row.account_id) != ACCOUNT_ID:
        out["reason"] = "SESSION721_MISSING_OR_WRONG_ACCOUNT"
        return out
    try:
        plaintext = load_channel_session_plaintext(row)
        out["SESSION721_DECRYPT_OK"] = True
        parse_session_envelope(plaintext)
        out["SESSION721_STRUCTURAL_VALID"] = True
    except (SessionDecryptionError, ValueError, Exception) as exc:  # noqa: BLE001
        out["reason"] = type(exc).__name__
        return out

    proof = await RealRubikaCandidateProver().prove_detailed(
        db,
        account_id=ACCOUNT_ID,
        session_id=PROVEN_SESSION_ID,
        expected_guid=expected_guid,
    )
    out["duration_ms"] = proof.duration_ms
    out["identity_guid_hash"] = _guid_hash(proof.identity_guid)
    out["SESSION721_AUTH_PASS"] = bool(proof.ok and proof.proof_status == PROOF_PASS)
    out["SESSION721_IDENTITY_MATCH"] = bool(proof.identity_match)
    out["SESSION721_FRESH_PROOF_PASS"] = (
        out["SESSION721_DECRYPT_OK"]
        and out["SESSION721_STRUCTURAL_VALID"]
        and out["SESSION721_AUTH_PASS"]
        and out["SESSION721_IDENTITY_MATCH"]
    )
    if not out["SESSION721_FRESH_PROOF_PASS"]:
        out["reason"] = proof.error_code or proof.proof_status
    return out


async def run() -> dict[str, Any]:
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.models import (
        Account,
        ChannelSession,
        RubikaLoginChallenge,
        RubikaLoginChallengeState,
        RubikaSessionStatus,
        SessionType,
    )
    from core_engine.services.account_runtime_status import compute_account_runtime_status
    from core_engine.services.crypto import SessionDecryptionError
    from core_engine.services.rubika_l17_automation import DISCOVERY_SCOPE_ALL_ELIGIBLE
    from core_engine.services.session_storage import load_channel_session_plaintext
    from workers.rubika_worker_discovery import (
        MODE_DYNAMIC,
        get_dispatch_eligible_rubika_account_ids,
        resolve_actual_worker_account_ids,
    )

    db = SessionLocal()
    try:
        db.autoflush = False

        def _forbid(*_a, **_k):
            raise RuntimeError("M2_PREMUTATION_WRITE_FORBIDDEN")

        db.commit = _forbid  # type: ignore[method-assign]
        db.flush = _forbid  # type: ignore[method-assign]
        db.add = _forbid  # type: ignore[method-assign]
        db.delete = _forbid  # type: ignore[method-assign]
        db.merge = _forbid  # type: ignore[method-assign]
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.execute(text("SET TRANSACTION READ ONLY"))

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
        a2_rows = (
            db.query(ChannelSession)
            .filter(
                ChannelSession.account_id == ACCOUNT_ID,
                ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            )
            .order_by(ChannelSession.id.asc())
            .all()
        )
        a2_sessions = [
            {"id": int(r.id), "status": r.session_status.value if r.session_status else None}
            for r in a2_rows
        ]
        account = db.query(Account).filter(Account.id == ACCOUNT_ID).first()
        expected_guid = str(account.rubika_guid or "").strip() or None if account else None

        row721 = next((r for r in a2_rows if int(r.id) == PROVEN_SESSION_ID), None)
        row2 = next((r for r in a2_rows if int(r.id) == INVALID_SESSION_ID), None)

        session2_decrypt_failed = False
        if row2 is not None:
            try:
                load_channel_session_plaintext(row2)
                session2_decrypt_failed = False
            except SessionDecryptionError:
                session2_decrypt_failed = True
            except Exception:  # noqa: BLE001
                session2_decrypt_failed = True

        a2_active_n = sum(
            1 for r in a2_rows if r.session_status == RubikaSessionStatus.ACTIVE
        )

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
        queue2 = _queue_len(ACCOUNT_ID)
        runtime = compute_account_runtime_status(db, account, worker_covered=False)

        proof = await _fresh_proof(db, expected_guid)

        # Safe metadata-only Account2 rollback manifest (no secrets).
        def _sess_meta(row: Any) -> dict[str, Any]:
            if row is None:
                return {"missing": True}
            return {
                "session_id": int(row.id),
                "account_id": int(row.account_id),
                "session_status": row.session_status.value if row.session_status else None,
                "key_version": row.key_version,
                "has_ciphertext": bool(row.ciphertext),
                "ciphertext_len": len(row.ciphertext) if row.ciphertext else 0,
                "identity_guid_hash": _guid_hash(row.identity_guid),
                "validation_error_code": row.validation_error_code,
                "validated_at": row.validated_at.isoformat() if row.validated_at else None,
                "invalidated_at": row.invalidated_at.isoformat() if row.invalidated_at else None,
            }

        manifest = {
            "account_id": ACCOUNT_ID,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": "M2_PREMUTATION",
            "session721": _sess_meta(row721),
            "session2": _sess_meta(row2),
            "account_identity": {
                "account_status": account.status.value
                if account and hasattr(account.status, "value")
                else None,
                "rubika_guid_hash": _guid_hash(account.rubika_guid) if account else None,
                "rubika_identity_status": account.rubika_identity_status.value
                if account and account.rubika_identity_status
                else None,
                "rubika_identity_verified_at": account.rubika_identity_verified_at.isoformat()
                if account and account.rubika_identity_verified_at
                else None,
            },
            "pre_actives": actives,
            "pre_workers": workers,
            "ACCOUNT2_ROLLBACK_READY": True,
            "restore_semantics": {
                "session721": "rollback_legacy_promotion if promoted to ACTIVE",
                "session2": "rollback_decrypt_failed_classification to legacy_unclassified",
                "scope": "Account2 only; never touch 13/23/27/74/12/19/81/92",
            },
        }
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ROLLBACK.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

        row721_status = (
            row721.session_status.value if row721 and row721.session_status else None
        )
        row2_status = row2.session_status.value if row2 and row2.session_status else None
        resume_partial = (
            a2_active_n == 1
            and actives.get(ACCOUNT_ID) == PROVEN_SESSION_ID
            and row721_status == "active"
            and row2_status == "legacy_unclassified"
            and session2_decrypt_failed
            and all(actives.get(a) == sid for a, sid in EXPECTED_ACTIVES.items())
        )
        fresh_path = a2_active_n == 0 and actives == EXPECTED_ACTIVES
        protected_ok = all(actives.get(a) == sid for a, sid in EXPECTED_ACTIVES.items())
        # Fresh: no A2 ACTIVE. Resume: sole ACTIVE is 721 after partial prior promote.
        baseline_actives_ok = fresh_path or (
            resume_partial and protected_ok and actives.get(2) == PROVEN_SESSION_ID
        )

        gates = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "READ_ONLY": True,
            "TARGET_ACCOUNT_ID": ACCOUNT_ID,
            "ACCOUNT2_PRE_SESSION_ROWS": a2_sessions,
            "GLOBAL_ACTIVE_SESSION_COUNT": len(actives),
            "ACTIVES": actives,
            "ACTUAL_WORKER_IDS": workers,
            "SESSION721_DECRYPT_OK": proof["SESSION721_DECRYPT_OK"],
            "SESSION721_STRUCTURAL_VALID": proof["SESSION721_STRUCTURAL_VALID"],
            "SESSION721_AUTH_PASS": proof["SESSION721_AUTH_PASS"],
            "SESSION721_IDENTITY_MATCH": proof["SESSION721_IDENTITY_MATCH"],
            "SESSION721_FRESH_PROOF_PASS": proof["SESSION721_FRESH_PROOF_PASS"],
            "SESSION2_DECRYPT_FAILED_CONFIRMED": session2_decrypt_failed,
            "NO_ACCOUNT2_ACTIVE_SESSION": a2_active_n == 0,
            "M2_RESUME_PARTIAL": resume_partial,
            "M2_FRESH_PATH": fresh_path,
            "ACCOUNT2_SOLE_ACTIVE_721": (
                a2_active_n == 1 and actives.get(ACCOUNT_ID) == PROVEN_SESSION_ID
            ),
            "NO_ACTIVE_OTP": open_ch == 0,
            "NO_QUEUE_ACTIVITY": queue2 == 0,
            "ACCOUNT2_RUNTIME_STATUS": runtime.runtime_status,
            "ACCOUNT2_RUNTIME_REASON": runtime.reason_code,
            "BASELINE_ACTIVES_OK": baseline_actives_ok,
            "BASELINE_WORKERS_OK": workers == EXPECTED_WORKERS
            or (
                # After promote, Account2 may already be dynamically covered.
                resume_partial
                and all(w in workers for w in EXPECTED_WORKERS)
                and set(workers).issubset(set(EXPECTED_WORKERS + [ACCOUNT_ID]))
            ),
            "SESSION_INVENTORY_OK": {s["id"] for s in a2_sessions} == {2, 721},
            "proof_meta": {
                "identity_guid_hash": proof.get("identity_guid_hash"),
                "duration_ms": proof.get("duration_ms"),
                "reason": proof.get("reason"),
            },
            "queue2": queue2,
            "open_challenge_count": open_ch,
            "msg": int(db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0),
            "ch": int(
                db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar() or 0
            ),
            "ACCOUNT2_ROLLBACK_READY": True,
            "rollback_manifest_path": str(ROLLBACK),
        }
        required_common = [
            "SESSION721_FRESH_PROOF_PASS",
            "SESSION721_AUTH_PASS",
            "SESSION721_IDENTITY_MATCH",
            "SESSION2_DECRYPT_FAILED_CONFIRMED",
            "NO_ACTIVE_OTP",
            "NO_QUEUE_ACTIVITY",
            "BASELINE_ACTIVES_OK",
            "BASELINE_WORKERS_OK",
            "SESSION_INVENTORY_OK",
        ]
        if resume_partial:
            required = required_common + ["M2_RESUME_PARTIAL", "ACCOUNT2_SOLE_ACTIVE_721"]
        else:
            required = required_common + ["NO_ACCOUNT2_ACTIVE_SESSION", "M2_FRESH_PATH"]
        failures = [k for k in required if not gates.get(k)]
        gates["failures"] = failures
        gates["M2_PREMUTATION_GATES_PASS"] = len(failures) == 0
        return gates
    finally:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()


def main() -> int:
    art = asyncio.run(run())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(art, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(art, ensure_ascii=False, indent=2, default=str))
    return 0 if art.get("M2_PREMUTATION_GATES_PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
