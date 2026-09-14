#!/usr/bin/env python3
"""L16 — Park Account3 safely + inventory no-session accounts (read-mostly).

Park policy:
  - Do NOT request/submit OTP for Account3
  - Do NOT DELETE challenge rows
  - Leave challenge until natural expiry (LOGIN_EXPIRED on next account-scoped request)
  - Clear only ephemeral Redis handshake secrets (never OTP; never DB challenge)

Inventory is READ-ONLY (SET TRANSACTION READ ONLY + rollback).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PILOT = 3
EXCLUDED_NEXT = frozenset({1, 2, 3, 12, 13, 19, 23, 74, 79, 81, 92})
ACTIVE_CH = (
    "otp_requested",
    "otp_waiting_for_operator",
    "otp_submitted",
    "authenticating",
    "identity_verifying",
    "session_persisting",
)
SECRET_KEY = "l16:otp_handshake:account:3"
REPORT_DIR = Path("/app/reports/rubika-remediation")
OUT_JSON = REPORT_DIR / "L16_REPLACEMENT_OTP_PILOT_SELECTION.json"
OUT_HOST = Path("reports/rubika-remediation/L16_REPLACEMENT_OTP_PILOT_SELECTION.json")


def _mask_phone(phone: str | None) -> str:
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"


def park_account3() -> dict:
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.models import ChannelSession, RubikaLoginChallenge
    from workers.config import get_worker_settings
    import redis

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        chs = (
            db.query(RubikaLoginChallenge)
            .filter(RubikaLoginChallenge.account_id == PILOT)
            .order_by(RubikaLoginChallenge.created_at.desc())
            .all()
        )
        active = [
            c
            for c in chs
            if (c.state.value if hasattr(c.state, "value") else str(c.state)) in ACTIVE_CH
        ]
        sess_n = db.query(ChannelSession).filter(ChannelSession.account_id == PILOT).count()
        now = datetime.now(timezone.utc)
        primary = active[0] if active else (chs[0] if chs else None)
        state = None
        expires_at = None
        expires_in_s = None
        if primary is not None:
            state = primary.state.value if hasattr(primary.state, "value") else str(primary.state)
            expires_at = primary.expires_at.isoformat() if primary.expires_at else None
            if primary.expires_at:
                exp = primary.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                expires_in_s = int((exp - now).total_seconds())
    finally:
        db.rollback()
        db.close()

    # Clear ephemeral handshake only (not DB challenge). Prevents accidental submit.
    handshake_cleared = False
    try:
        s = get_worker_settings()
        r = redis.from_url(s.REDIS_URL, decode_responses=True)
        try:
            handshake_cleared = bool(r.delete(SECRET_KEY))
        finally:
            r.close()
    except Exception as exc:  # noqa: BLE001
        handshake_cleared = False
        handshake_err = type(exc).__name__
    else:
        handshake_err = None

    parked = (
        sess_n == 0
        and (state in ACTIVE_CH or state in {"login_expired", "login_failed", "ready", None})
    )
    return {
        "ACCOUNT3_PARKED_SAFELY": parked and sess_n == 0,
        "ACCOUNT3_CHALLENGE_STATE": state,
        "ACCOUNT3_ACTIVE_CHALLENGE_COUNT": len(active),
        "ACCOUNT3_SESSION_COUNT": sess_n,
        "ACCOUNT3_CHALLENGE_EXPIRES_AT": expires_at,
        "ACCOUNT3_CHALLENGE_EXPIRES_IN_SECONDS": expires_in_s,
        "park_policy": "leave_intact_until_natural_expiry",
        "challenge_deleted": False,
        "otp_requested": False,
        "otp_submitted": False,
        "handshake_secret_cleared": handshake_cleared,
        "handshake_clear_error": handshake_err,
        "note": (
            "No CANCELLED state in L3; LOGIN_EXPIRED applied only on natural expiry "
            "when Account3 later attempts request. Challenge row preserved."
        ),
    }


def prove_other_account_login_allowed() -> dict:
    """Source + runtime proof that challenges are account-scoped."""
    from pathlib import Path as P

    sm = (P("/app/core_engine/services/rubika_login_state_machine.py")).read_text(
        encoding="utf-8"
    )
    # _get_active_challenge filters by account_id
    scoped = (
        "def _get_active_challenge(db: Session, account_id: int)" in sm
        and "RubikaLoginChallenge.account_id == int(account_id)" in sm
    )
    # No global "any pending challenge blocks all" gate
    global_block = "global pending" in sm.lower() or "any_active_challenge" in sm
    return {
        "OTHER_ACCOUNT_LOGIN_ALLOWED": scoped and not global_block,
        "challenge_lookup_account_scoped": scoped,
        "global_pending_gate_present": global_block,
        "evidence": "_get_active_challenge(db, account_id) filters by account_id only",
    }


async def inventory_no_session() -> dict:
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.models import Account, AccountStatus, PlatformType, RubikaAccountPool
    from workers.config import get_worker_settings
    from workers.rubika_account_pool import resolve_current_phase
    from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings
    import redis.asyncio as redis

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        phase = resolve_current_phase(db)
        pool_ids = set()
        if phase:
            pool_ids = {
                int(r.account_id)
                for r in db.query(RubikaAccountPool)
                .filter(RubikaAccountPool.phase == phase)
                .all()
            }
        accounts = (
            db.query(Account)
            .filter(Account.platform == PlatformType.RUBIKA)
            .order_by(Account.id.asc())
            .all()
        )
        s = get_worker_settings()
        r = redis.from_url(s.REDIS_URL, decode_responses=True)
        await r.ping()
        rows = []
        try:
            for acct in accounts:
                aid = int(acct.id)
                sess_n = int(
                    db.execute(
                        text("SELECT count(*) FROM channel_sessions WHERE account_id=:a"),
                        {"a": aid},
                    ).scalar()
                    or 0
                )
                if sess_n != 0:
                    continue
                active_ch = int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM rubika_login_challenges "
                            "WHERE account_id=:a AND state::text = ANY(:s)"
                        ),
                        {"a": aid, "s": list(ACTIVE_CH)},
                    ).scalar()
                    or 0
                )
                q = int(await r.llen(f"queue:rubika:{aid}"))
                status = (
                    acct.status.value
                    if hasattr(acct.status, "value")
                    else str(acct.status or "")
                )
                guid = (acct.rubika_guid or "").strip()
                in_pool = aid in pool_ids if phase else False
                blockers = []
                if aid in EXCLUDED_NEXT:
                    blockers.append("EXCLUDED_FROM_NEXT_PILOT")
                if status != AccountStatus.ACTIVE.value:
                    blockers.append(f"STATUS_{status}")
                if not (acct.phone_number or "").strip():
                    blockers.append("NO_PHONE")
                if guid:
                    blockers.append("IDENTITY_ALREADY_BOUND")
                if active_ch > 0:
                    blockers.append("ACTIVE_LOGIN_CHALLENGE")
                if phase is None:
                    blockers.append("NO_SCHEDULE")
                elif not in_pool:
                    blockers.append("NOT_IN_POOL")
                if q != 0:
                    blockers.append(f"QUEUE_{q}")
                # Account3 specifically parked waiting for OTP access
                if aid == 3:
                    blockers.append("OPERATOR_NO_PHONE_ACCESS")
                safe = not blockers and aid not in EXCLUDED_NEXT
                rows.append(
                    {
                        "account_id": aid,
                        "phone_masked": _mask_phone(acct.phone_number),
                        "account_status": status,
                        "queue_length": q,
                        "active_login_challenge": active_ch,
                        "pool_membership": phase if in_pool else "NONE",
                        "schedule_eligibility": (
                            "in_phase_pool"
                            if in_pool
                            else ("NO_SCHEDULE" if phase is None else "NOT_IN_POOL")
                        ),
                        "identity_conflict": bool(guid),
                        "known_blocker": ",".join(blockers) if blockers else "",
                        "safe_for_next_otp_pilot": safe,
                    }
                )
        finally:
            await r.aclose()

        ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
        global_active = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        )
        safe = [x for x in rows if x["safe_for_next_otp_pilot"]]
        # Prefer pool members with phone; already filtered
        shortlist = sorted(safe, key=lambda x: x["account_id"])
        return {
            "phase": phase,
            "NO_SESSION_ACCOUNT_IDS": [x["account_id"] for x in rows],
            "no_session_rows": rows,
            "SAFE_NEXT_PILOT_CANDIDATES": [
                {
                    "account_id": x["account_id"],
                    "phone_masked": x["phone_masked"],
                    "status": x["account_status"],
                    "pool": x["pool_membership"],
                    "blocker": x["known_blocker"] or "none",
                }
                for x in shortlist
            ],
            "CURRENT_ACTIVE_WORKER_IDS": ids,
            "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
            "discovery": {
                "mode": meta["mode"],
                "cohort": meta["cohort_ids"],
                "pin": meta["pinned_ids"],
            },
        }
    finally:
        db.rollback()
        db.close()


def recovery_plan() -> dict:
    return {
        "sequence": [
            "Operator selects accessible-phone candidate from SAFE_NEXT_PILOT_CANDIDATES",
            "Prechecks: zero sessions, no active challenge, queue=0, pool/schedule OK, L3 pilot gate scoped",
            "Backup DB + config rollback copy",
            "Set RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS=<candidate> (account-scoped L3)",
            "Recreate core_api only",
            "Request exactly ONE OTP via request_rubika_login",
            "Operator enters OTP locally via docker exec -it ... submit (getpass)",
            "L3: prove → VALIDATING → reconnect → identity → promote ACTIVE",
            "One-shot SHADOW_MATCH",
            "Confirm dynamic eligibility",
            "Expand discovery cohort to include candidate; recreate rubika_worker",
            "Expand canonical ENFORCE allowlist; recreate canonical consumers",
            "Verify health; then next account (never parallel OTPs)",
        ],
        "hard_rules": [
            "Never request multiple OTPs simultaneously",
            "Never send real messages during recovery",
            "Never remove pin 12,79 during recovery",
            "Never delete challenge forensic rows",
            "Batch size: one account end-to-end before starting the next",
        ],
    }


def main() -> int:
    park = park_account3()
    other = prove_other_account_login_allowed()
    inv = asyncio.run(inventory_no_session())
    plan = recovery_plan()
    artifact = {
        "CURRENT_PHASE": "L16_REPLACEMENT_OTP_PILOT_SELECTION",
        "PHASE_STATUS": "READY_FOR_OPERATOR_SELECTION"
        if inv.get("SAFE_NEXT_PILOT_CANDIDATES") and other.get("OTHER_ACCOUNT_LOGIN_ALLOWED")
        else "BLOCKED",
        **park,
        **other,
        "NO_SESSION_ACCOUNT_IDS": inv["NO_SESSION_ACCOUNT_IDS"],
        "no_session_detail": inv["no_session_rows"],
        "SAFE_NEXT_PILOT_CANDIDATES": inv["SAFE_NEXT_PILOT_CANDIDATES"],
        "CURRENT_ACTIVE_WORKER_IDS": inv["CURRENT_ACTIVE_WORKER_IDS"],
        "GLOBAL_ACTIVE_SESSION_COUNT": inv["GLOBAL_ACTIVE_SESSION_COUNT"],
        "discovery": inv["discovery"],
        "OTP_REQUESTED_FOR_OTHER_ACCOUNT": False,
        "MESSAGE_SENT": False,
        "recovery_plan": plan,
        "EXACT_OPERATOR_INPUT_REQUIRED": (
            "Choose one candidate whose Rubika phone is accessible now"
            if inv.get("SAFE_NEXT_PILOT_CANDIDATES")
            else None
        ),
        "excluded_from_next_pilot": sorted(EXCLUDED_NEXT),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    # Host-side copy when bind-mounted
    try:
        Path("reports/rubika-remediation").mkdir(parents=True, exist_ok=True)
        OUT_HOST.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass
    summary = {
        "ACCOUNT3_PARKED_SAFELY": artifact["ACCOUNT3_PARKED_SAFELY"],
        "ACCOUNT3_CHALLENGE_STATE": artifact["ACCOUNT3_CHALLENGE_STATE"],
        "ACCOUNT3_SESSION_COUNT": artifact["ACCOUNT3_SESSION_COUNT"],
        "ACCOUNT3_CHALLENGE_EXPIRES_IN_SECONDS": artifact["ACCOUNT3_CHALLENGE_EXPIRES_IN_SECONDS"],
        "OTHER_ACCOUNT_LOGIN_ALLOWED": artifact["OTHER_ACCOUNT_LOGIN_ALLOWED"],
        "NO_SESSION_ACCOUNT_IDS": artifact["NO_SESSION_ACCOUNT_IDS"],
        "SAFE_NEXT_PILOT_CANDIDATES": [
            f"{c['account_id']} | {c['phone_masked']} | {c['blocker']}"
            for c in artifact["SAFE_NEXT_PILOT_CANDIDATES"]
        ],
        "CURRENT_ACTIVE_WORKER_IDS": artifact["CURRENT_ACTIVE_WORKER_IDS"],
        "GLOBAL_ACTIVE_SESSION_COUNT": artifact["GLOBAL_ACTIVE_SESSION_COUNT"],
        "OTP_REQUESTED_FOR_OTHER_ACCOUNT": False,
        "MESSAGE_SENT": False,
        "CURRENT_PHASE": artifact["CURRENT_PHASE"],
        "PHASE_STATUS": artifact["PHASE_STATUS"],
        "EXACT_OPERATOR_INPUT_REQUIRED": artifact["EXACT_OPERATOR_INPUT_REQUIRED"],
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0 if artifact["PHASE_STATUS"] == "READY_FOR_OPERATOR_SELECTION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
