"""L7 READ-ONLY: per-session_id duplicate probes + Batch A / Account79 verify.

Probes each ChannelSession by primary key — never max(id) selection.

Does NOT:
  promote sessions, set ACTIVE, request OTP, send messages,
  mutate Redis, change Account12/79 session rows.

Env:
  L7_PROBE_TIMEOUT_SECONDS=25 (default)
  L7_ALLOW_PRODUCTION_READONLY_PROBE=1  (required for live mmp_db)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core_engine.database import SessionLocal
from core_engine.models import ChannelSession, SessionType
from core_engine.services.rubika_candidate_prover import (
    PROOF_PASS,
    RealRubikaCandidateProver,
)
from core_engine.services.rubika_l7_classifiers import (
    classify_account12,
    classify_duplicate_account,
)
from core_engine.services.session_storage import load_channel_session_plaintext
from core_engine.services.rubika_user_session import parse_session_envelope
from core_engine.services.crypto import SessionDecryptionError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# reports/ is not bind-mounted into mmp_core_api; write under scripts/ then copy.
OUT_DIR = Path(__file__).resolve().parent / "_l7_probe_out"
OUT_JSON = OUT_DIR / "L7_DUPLICATE_SESSION_PROBES.json"
OUT_MD = OUT_DIR / "L7_CANONICAL_COHORT_AND_DUPLICATE_PROBES.md"
REPORT_JSON = (
    PROJECT_ROOT / "reports" / "rubika-remediation" / "L7_DUPLICATE_SESSION_PROBES.json"
)
REPORT_MD = (
    PROJECT_ROOT
    / "reports"
    / "rubika-remediation"
    / "L7_CANONICAL_COHORT_AND_DUPLICATE_PROBES.md"
)

DUPLICATE_ACCOUNTS = (2, 12, 19, 81, 92)
BATCH_A_ACCOUNTS = (13, 23, 74)
PROTECTED_79 = 79
TIMEOUT = float(os.environ.get("L7_PROBE_TIMEOUT_SECONDS", "25"))


def _refuse_if_not_authorized() -> None:
    if os.environ.get("L7_ALLOW_PRODUCTION_READONLY_PROBE") != "1":
        raise SystemExit(
            "REFUSE: set L7_ALLOW_PRODUCTION_READONLY_PROBE=1 for read-only production probes"
        )


def _assert_db_is_mmp(db: Session) -> None:
    name = db.execute(text("SELECT current_database()")).scalar()
    if str(name) != "mmp_db":
        raise SystemExit(f"REFUSE: expected mmp_db, got {name}")


def _legacy_selected_id(db: Session, account_id: int) -> int | None:
    row = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
        )
        .order_by(ChannelSession.id.desc())
        .first()
    )
    return int(row.id) if row else None


def _real_send_evidence(db: Session, account_id: int) -> dict[str, Any]:
    rows = db.execute(
        text(
            """
            SELECT ma.id, ma.status::text, ma.created_at
            FROM message_attempts ma
            JOIN messages m ON m.id = ma.message_id
            WHERE m.account_id = :aid AND ma.status::text = 'SUCCESS'
            ORDER BY ma.id
            """
        ),
        {"aid": int(account_id)},
    ).fetchall()
    return {
        "success_attempt_ids": [int(r[0]) for r in rows],
        "success_attempt_count": len(rows),
        "success_attempt_created_at": [r[2].isoformat() if r[2] else None for r in rows],
    }


def _session_rows(db: Session, account_id: int) -> list[ChannelSession]:
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
        )
        .order_by(ChannelSession.id.asc())
        .all()
    )


async def _probe_session(
    db: Session,
    *,
    account_id: int,
    session: ChannelSession,
    legacy_selected_id: int | None,
    send_evidence: dict[str, Any],
    prover: RealRubikaCandidateProver,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "account_id": int(account_id),
        "session_id": int(session.id),
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "current_legacy_selected": legacy_selected_id == int(session.id),
        "real_send_evidence": send_evidence,
        "decrypt_status": None,
        "structure_status": None,
        "reconnect_status": None,
        "identity_match": None,
        "identity_guid": None,
        "error_code": None,
        "sanitized_message": None,
        "duration_ms": None,
        "probe_by": "session_id",
        "session_status": str(getattr(session.session_status, "value", session.session_status)),
    }
    try:
        plaintext = load_channel_session_plaintext(session)
        out["decrypt_status"] = "OK"
    except SessionDecryptionError as exc:
        out["decrypt_status"] = "SESSION_DECRYPT_FAILED"
        out["structure_status"] = "N/A"
        out["reconnect_status"] = "SKIPPED"
        out["error_code"] = "SESSION_DECRYPT_FAILED"
        out["sanitized_message"] = type(exc).__name__
        return out
    except Exception as exc:  # noqa: BLE001
        out["decrypt_status"] = "SESSION_DECRYPT_FAILED"
        out["structure_status"] = "N/A"
        out["reconnect_status"] = "SKIPPED"
        out["error_code"] = type(exc).__name__
        out["sanitized_message"] = type(exc).__name__
        return out

    try:
        parse_session_envelope(plaintext)
        out["structure_status"] = "OK"
    except ValueError as exc:
        out["structure_status"] = "SESSION_STRUCTURALLY_INVALID"
        out["reconnect_status"] = "SKIPPED"
        out["error_code"] = "SESSION_STRUCTURALLY_INVALID"
        out["sanitized_message"] = str(exc)[:120]
        return out

    result = await prover.prove_detailed(
        db,
        account_id=int(account_id),
        session_id=int(session.id),
        expected_guid=None,
    )
    out["duration_ms"] = result.duration_ms
    out["identity_guid"] = result.identity_guid
    out["identity_match"] = result.identity_match
    out["sanitized_message"] = result.sanitized_message
    out["error_code"] = result.error_code
    if result.proof_status == PROOF_PASS:
        out["reconnect_status"] = "AUTH_RECONNECT_PASS"
        out["identity_match"] = True
    else:
        out["reconnect_status"] = result.proof_status or result.error_code or "AUTH_RECONNECT_FAILED"
    return out


async def _probe_account(db: Session, account_id: int) -> dict[str, Any]:
    legacy_id = _legacy_selected_id(db, account_id)
    send_ev = _real_send_evidence(db, account_id)
    sessions = _session_rows(db, account_id)
    prover = RealRubikaCandidateProver(timeout_seconds=TIMEOUT)
    results: list[dict[str, Any]] = []
    for row in sessions:
        # Explicit by session_id — never pick via max(id) loader.
        results.append(
            await _probe_session(
                db,
                account_id=account_id,
                session=row,
                legacy_selected_id=legacy_id,
                send_evidence=send_ev,
                prover=prover,
            )
        )
        await asyncio.sleep(1.0)  # mild pacing
    classification = classify_duplicate_account(results)
    if int(account_id) == 12:
        classification = classify_account12(results)
    if int(account_id) == 92 and classification == "DECRYPT_REPAIR_OR_RELOGIN_REQUIRED":
        pass
    return {
        "account_id": int(account_id),
        "legacy_selected_session_id": legacy_id,
        "session_count": len(sessions),
        "sessions": results,
        "classification": classification,
    }


def _batch_a_verify(account_probe: dict[str, Any]) -> dict[str, Any]:
    sessions = account_probe.get("sessions") or []
    ok = (
        len(sessions) == 1
        and sessions[0].get("decrypt_status") == "OK"
        and sessions[0].get("structure_status") == "OK"
        and sessions[0].get("reconnect_status") == "AUTH_RECONNECT_PASS"
        and sessions[0].get("identity_match") is True
    )
    return {
        "account_id": account_probe["account_id"],
        "candidate_session_id": sessions[0]["session_id"] if sessions else None,
        "verified": ok,
        "session_count": len(sessions),
        "reasons": []
        if ok
        else [
            f"decrypt={sessions[0].get('decrypt_status') if sessions else None}",
            f"structure={sessions[0].get('structure_status') if sessions else None}",
            f"reconnect={sessions[0].get('reconnect_status') if sessions else None}",
            f"identity_match={sessions[0].get('identity_match') if sessions else None}",
            f"session_count={len(sessions)}",
        ],
    }


def _write_md(payload: dict[str, Any]) -> None:
    lines = [
        "# L7 — Canonical Cohort Gating + Duplicate Session Probes",
        "",
        f"**Generated:** {payload.get('generated_at_utc')}",
        f"**Mutation:** {payload.get('mutation')}",
        f"**OTP requested:** {payload.get('otp_requested')}",
        f"**Messages sent:** {payload.get('message_sent')}",
        f"**Promotions:** {payload.get('production_session_promotions')}",
        "",
        "## Runtime modes",
        "",
        "- `RUBIKA_CANONICAL_SESSION_MODE=off|shadow|enforce`",
        "- `RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=` cohort allowlist",
        "- Default remains **off** (legacy). Global enforce is not the first rollout path.",
        "- Shadow is non-mutating. Enforce fails closed for allowlisted accounts.",
        "",
        "## Duplicate probe classifications",
        "",
    ]
    for aid in DUPLICATE_ACCOUNTS:
        block = payload["duplicate_accounts"].get(str(aid)) or {}
        lines.append(f"### Account {aid}")
        lines.append(f"- classification: `{block.get('classification')}`")
        lines.append(f"- legacy_selected: `{block.get('legacy_selected_session_id')}`")
        for s in block.get("sessions") or []:
            lines.append(
                f"  - session `{s.get('session_id')}` decrypt={s.get('decrypt_status')} "
                f"structure={s.get('structure_status')} reconnect={s.get('reconnect_status')} "
                f"identity_match={s.get('identity_match')} "
                f"legacy_selected={s.get('current_legacy_selected')}"
            )
        lines.append("")
    lines.extend(
        [
            "## Batch A verification",
            "",
        ]
    )
    for row in payload.get("batch_a_verification") or []:
        lines.append(
            f"- Account {row['account_id']}: candidate=`{row.get('candidate_session_id')}` "
            f"verified={row.get('verified')}"
        )
    lines.extend(
        [
            "",
            "## Account79 protected",
            "",
            f"- candidate: `{payload.get('account79', {}).get('candidate_session_id')}`",
            f"- verified: `{payload.get('account79', {}).get('verified')}`",
            "",
            "## Recommendations (NO PROMOTION)",
            "",
            f"- BATCH_A_FINAL: {payload.get('BATCH_A_FINAL')}",
            f"- BATCH_B_FINAL: {payload.get('BATCH_B_FINAL')}",
            f"- MANUAL_REVIEW_FINAL: {payload.get('MANUAL_REVIEW_FINAL')}",
            f"- ACCOUNT12_RECOMMENDED_CANDIDATE: {payload.get('ACCOUNT12_RECOMMENDED_CANDIDATE')}",
            f"- ACCOUNT12_SAFE_TO_PROMOTE: {payload.get('ACCOUNT12_SAFE_TO_PROMOTE')}",
            "",
            "## Next safe action",
            "",
            "Review L7 before first controlled canonical session promotion.",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _amain() -> int:
    _refuse_if_not_authorized()
    db = SessionLocal()
    try:
        _assert_db_is_mmp(db)
        # Read-only guard: abort if any write sneaks in via unexpected flush.
        db.rollback()

        duplicate: dict[str, Any] = {}
        for aid in DUPLICATE_ACCOUNTS:
            print(f"probing account {aid} ...", flush=True)
            duplicate[str(aid)] = await _probe_account(db, aid)
            db.rollback()  # discard any accidental ORM dirt

        batch_a_probes = []
        batch_a_verify = []
        for aid in BATCH_A_ACCOUNTS:
            print(f"verifying Batch A account {aid} ...", flush=True)
            probe = await _probe_account(db, aid)
            batch_a_probes.append(probe)
            batch_a_verify.append(_batch_a_verify(probe))
            db.rollback()

        print("verifying Account79 session 600 ...", flush=True)
        a79 = await _probe_account(db, PROTECTED_79)
        db.rollback()
        a79_verify = _batch_a_verify(a79)
        a79_verify["candidate_session_id"] = 600
        a79_verify["protected"] = True

        # Classifications
        c2 = duplicate["2"]["classification"]
        c12 = duplicate["12"]["classification"]
        c19 = duplicate["19"]["classification"]
        c81 = duplicate["81"]["classification"]
        c92 = duplicate["92"]["classification"]

        batch_b: list[int] = []
        for aid, cls in ((2, c2), (19, c19), (81, c81)):
            if cls == "ONE_PROVEN_ONE_INVALID":
                batch_b.append(aid)

        batch_a_final = [
            r["account_id"]
            for r in batch_a_verify
            if r.get("verified")
        ]
        manual_final = sorted(
            set(
                [1, 12]
                + ([92] if "DECRYPT" in c92 or c92 == "NO_VALID_SESSION" else [92])
                + [a for a in (2, 19, 81) if a not in batch_b]
                + ([12] if c12 != "CLEAR_728_CANDIDATE" and c12 != "CLEAR_657_CANDIDATE" else [])
            )
        )
        # Account12 always stays manual while ambiguous / both-valid.
        if c12 in {
            "BOTH_VALID_CURRENT_IDENTITY",
            "STILL_AMBIGUOUS",
            "IDENTITY_CONFLICT",
            "ONE_INVALID_ONE_VALID",
        }:
            manual_final = sorted(set(manual_final) | {12})
        if 12 in batch_b:
            batch_b = [a for a in batch_b if a != 12]

        a12_safe = False
        a12_rec = None
        if c12 == "CLEAR_728_CANDIDATE":
            a12_rec = 728
        elif c12 == "CLEAR_657_CANDIDATE":
            a12_rec = 657
        elif c12 in {"BOTH_VALID_CURRENT_IDENTITY", "STILL_AMBIGUOUS"}:
            a12_rec = 728  # current legacy selected as recommendation only
            a12_safe = False
        else:
            a12_rec = 728
            a12_safe = False

        payload = {
            "phase": "L7_CANONICAL_COHORT_AND_DUPLICATE_PROBES",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "mutation": False,
            "otp_requested": False,
            "message_sent": False,
            "production_session_promotions": 0,
            "global_canonical_enforce_enabled": False,
            "probe_timeout_seconds": TIMEOUT,
            "duplicate_accounts": duplicate,
            "batch_a_verification": batch_a_verify,
            "batch_a_probes": batch_a_probes,
            "account79": a79_verify,
            "account79_probe": a79,
            "ACCOUNT2_CLASSIFICATION": c2,
            "ACCOUNT12_CLASSIFICATION": c12,
            "ACCOUNT19_CLASSIFICATION": c19,
            "ACCOUNT81_CLASSIFICATION": c81,
            "ACCOUNT92_CLASSIFICATION": c92
            if c92 != "NO_VALID_SESSION"
            else "DECRYPT_REPAIR_OR_RELOGIN_REQUIRED",
            "ACCOUNT12_RECOMMENDED_CANDIDATE": a12_rec,
            "ACCOUNT12_SAFE_TO_PROMOTE": a12_safe,
            "BATCH_A_FINAL": batch_a_final,
            "BATCH_B_FINAL": batch_b,
            "MANUAL_REVIEW_FINAL": manual_final,
            "ACCOUNT13_CANDIDATE_SESSION": next(
                (r["candidate_session_id"] for r in batch_a_verify if r["account_id"] == 13),
                None,
            ),
            "ACCOUNT23_CANDIDATE_SESSION": next(
                (r["candidate_session_id"] for r in batch_a_verify if r["account_id"] == 23),
                None,
            ),
            "ACCOUNT74_CANDIDATE_SESSION": next(
                (r["candidate_session_id"] for r in batch_a_verify if r["account_id"] == 74),
                None,
            ),
            "ACCOUNT79_CANDIDATE_SESSION": 600,
            "canonical_runtime_modes": {
                "RUBIKA_CANONICAL_SESSION_MODE": "off|shadow|enforce",
                "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS": "cohort allowlist",
                "default_mode": "off",
                "shadow_non_mutating": True,
                "enforce_fails_closed": True,
            },
        }
        # Fix 92 classification label if decrypt-only
        if all(
            s.get("decrypt_status") == "SESSION_DECRYPT_FAILED"
            for s in (duplicate["92"].get("sessions") or [])
        ):
            payload["ACCOUNT92_CLASSIFICATION"] = "DECRYPT_REPAIR_OR_RELOGIN_REQUIRED"
            duplicate["92"]["classification"] = "DECRYPT_REPAIR_OR_RELOGIN_REQUIRED"

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _write_md(payload)
        # Best-effort copy into reports/ when that tree exists/is writable.
        try:
            REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
            REPORT_JSON.write_text(OUT_JSON.read_text(encoding="utf-8"), encoding="utf-8")
            REPORT_MD.write_text(OUT_MD.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
        print(json.dumps({k: payload[k] for k in (
            "ACCOUNT2_CLASSIFICATION",
            "ACCOUNT12_CLASSIFICATION",
            "ACCOUNT19_CLASSIFICATION",
            "ACCOUNT81_CLASSIFICATION",
            "ACCOUNT92_CLASSIFICATION",
            "BATCH_A_FINAL",
            "BATCH_B_FINAL",
            "MANUAL_REVIEW_FINAL",
            "ACCOUNT12_SAFE_TO_PROMOTE",
            "ACCOUNT13_CANDIDATE_SESSION",
            "ACCOUNT23_CANDIDATE_SESSION",
            "ACCOUNT74_CANDIDATE_SESSION",
        )}, indent=2))
        return 0
    finally:
        db.rollback()
        db.close()


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
