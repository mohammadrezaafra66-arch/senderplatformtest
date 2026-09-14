#!/usr/bin/env python3
"""L16 Account3 OTP request (ONE shot) + optional interactive submit.

Usage:
  request   — request exactly one OTP; persist provider handshake secrets in Redis TTL
  submit    — interactive getpass OTP; submit via L3; discard OTP immediately
  status    — read-only post-request / post-login status

OTP plaintext is never stored in DB, Redis, files, env, or reports.
Provider handshake secrets (phone_code_hash/keys) live only in Redis with short TTL.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import sys
from pathlib import Path

PILOT = 3
REPORT_DIR = Path("/app/reports/rubika-remediation")
REQUEST_ARTIFACT = REPORT_DIR / "L16_OTP_REQUEST.json"
SUBMIT_ARTIFACT = REPORT_DIR / "L16_OTP_SUBMIT.json"
SECRET_KEY = "l16:otp_handshake:account:3"
SECRET_TTL = 600
ACTIVE_CH = (
    "otp_requested",
    "otp_waiting_for_operator",
    "otp_submitted",
    "authenticating",
    "identity_verifying",
    "session_persisting",
)


def _mask_phone(phone: str | None) -> str:
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"


def _redis():
    from workers.config import get_worker_settings
    import redis

    s = get_worker_settings()
    return redis.from_url(s.REDIS_URL, decode_responses=True)


async def cmd_request() -> dict:
    from sqlalchemy import text

    from core_engine.config import get_settings
    from core_engine.database import SessionLocal
    from core_engine.models import ChannelSession, RubikaLoginChallenge
    from core_engine.services.rubika_login_live_provider import LiveRubikaLoginProvider
    from core_engine.services.rubika_login_state_machine import (
        account_uses_l3_login,
        request_rubika_login,
    )

    get_settings.cache_clear()
    if not account_uses_l3_login(PILOT):
        return {"OTP_REQUEST_PASS": False, "error": "PILOT_NOT_ON_L3", "PHASE_STATUS": "BLOCKED"}

    secret_store: dict = {}
    db = SessionLocal()
    try:
        before_sess = db.query(ChannelSession).filter(ChannelSession.account_id == PILOT).count()
        before_active = int(
            db.execute(
                text("SELECT count(*) FROM channel_sessions WHERE session_status::text='active'")
            ).scalar()
            or 0
        )
        before_msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)

        # Refuse if already pending — do not request second OTP
        pending = (
            db.query(RubikaLoginChallenge)
            .filter(
                RubikaLoginChallenge.account_id == PILOT,
                RubikaLoginChallenge.state.in_(list(ACTIVE_CH)),
            )
            .count()
        )
        if pending > 0:
            return {
                "OTP_REQUEST_PASS": False,
                "error": "OTP_ALREADY_PENDING",
                "PHASE_STATUS": "BLOCKED",
                "active_challenge_count": pending,
            }

        result = await request_rubika_login(
            db, PILOT, provider=LiveRubikaLoginProvider(), secret_store=secret_store
        )
        if not result.ok:
            db.commit()
            return {
                "OTP_REQUEST_PASS": False,
                "error_code": result.code,
                "challenge_id": result.challenge_id,
                "state": result.state,
                "PHASE_STATUS": "BLOCKED",
                "OTP_REQUESTED": True,
            }
        db.commit()
        challenge_id = str(result.challenge_id)

        # Persist handshake secrets only (never OTP) for interactive submit process
        r = _redis()
        try:
            blob = secret_store.get(challenge_id) or {}
            payload = json.dumps({"challenge_id": challenge_id, "secret_blob": blob})
            r.setex(SECRET_KEY, SECRET_TTL, payload)
        finally:
            r.close()
        secret_store.clear()

        # Post-request hard verify
        db2 = SessionLocal()
        try:
            db2.execute(text("SET TRANSACTION READ ONLY"))
            active_chs = (
                db2.query(RubikaLoginChallenge)
                .filter(
                    RubikaLoginChallenge.account_id == PILOT,
                    RubikaLoginChallenge.state.in_(list(ACTIVE_CH)),
                )
                .all()
            )
            sess_n = db2.query(ChannelSession).filter(ChannelSession.account_id == PILOT).count()
            active_sess = int(
                db2.execute(
                    text(
                        "SELECT count(*) FROM channel_sessions WHERE account_id=:a "
                        "AND session_status::text='active'"
                    ),
                    {"a": PILOT},
                ).scalar()
                or 0
            )
            global_active = int(
                db2.execute(
                    text(
                        "SELECT count(*) FROM channel_sessions "
                        "WHERE session_status::text='active'"
                    )
                ).scalar()
                or 0
            )
            msg = int(db2.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
            cols = [
                row[0]
                for row in db2.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='rubika_login_challenges'"
                    )
                ).all()
            ]
            ch = active_chs[0] if len(active_chs) == 1 else None
            ok = (
                len(active_chs) == 1
                and ch is not None
                and int(ch.account_id) == PILOT
                and str(ch.state.value) == "otp_waiting_for_operator"
                and sess_n == 0
                and before_sess == 0
                and active_sess == 0
                and global_active == 3
                and before_active == 3
                and msg == before_msg
                and "otp_code" not in cols
            )
            artifact = {
                "PILOT_ACCOUNT_ID": PILOT,
                "OTP_REQUEST_PASS": ok,
                "OTP_REQUESTED": True,
                "challenge_id": challenge_id,
                "challenge_state": ch.state.value if ch else None,
                "active_challenge_count": len(active_chs),
                "PILOT_SESSION_COUNT": sess_n,
                "PILOT_ACTIVE_SESSION_COUNT": active_sess,
                "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
                "MessageAttempt_count": msg,
                "PILOT_ACCOUNT_PHONE_MASKED": _mask_phone(ch.phone_e164 if ch else None),
                "otp_column_present": "otp_code" in cols,
                "canonical_allowlist_unchanged": True,
                "MESSAGE_SENT": False,
                "PHASE_STATUS": "WAITING_FOR_OTP" if ok else "BLOCKED",
                "WAITING_FOR_OTP_CODE": ok,
                "EXACT_OPERATOR_INPUT_REQUIRED": (
                    "Enter OTP for Account3 via: "
                    "docker exec -it -e PYTHONPATH=/app -w /app mmp_core_api "
                    "python scripts/_l16_request_and_wait_otp.py submit"
                    if ok
                    else None
                ),
            }
        finally:
            db2.rollback()
            db2.close()
        return artifact
    finally:
        db.close()


async def cmd_submit() -> dict:
    from sqlalchemy import text

    from core_engine.config import get_settings
    from core_engine.database import SessionLocal
    from core_engine.models import ChannelSession, RubikaLoginChallenge
    from core_engine.services.rubika_candidate_prover import resolve_canonical_candidate_prover
    from core_engine.services.rubika_login_live_provider import LiveRubikaLoginProvider
    from core_engine.services.rubika_login_state_machine import submit_rubika_login_code

    get_settings.cache_clear()
    r = _redis()
    try:
        raw = r.get(SECRET_KEY)
    finally:
        r.close()
    if not raw:
        return {
            "OTP_SUBMIT_PASS": False,
            "error": "HANDSHAKE_SECRET_EXPIRED_OR_MISSING",
            "PHASE_STATUS": "WAITING_FOR_OTP",
            "EXACT_OPERATOR_INPUT_REQUIRED": (
                "Handshake secrets expired. Do NOT auto-retry OTP. "
                "Operator must authorize a fresh OTP request."
            ),
        }
    data = json.loads(raw)
    challenge_id = data["challenge_id"]
    secret_blob = data.get("secret_blob") or {}
    secret_store = {challenge_id: secret_blob}

    print("Enter OTP for Account3:", flush=True)
    try:
        otp = getpass.getpass(prompt="")
    except Exception:
        return {
            "OTP_SUBMIT_PASS": False,
            "OTP_REQUEST_PASS": True,
            "PHASE_STATUS": "WAITING_FOR_OTP",
            "EXACT_OPERATOR_INPUT_REQUIRED": (
                "Enter OTP directly into the local Cursor/terminal workflow; "
                "do not place it in reports"
            ),
        }
    if not (otp or "").strip():
        try:
            del otp
        except Exception:
            pass
        return {
            "OTP_SUBMIT_PASS": False,
            "error": "EMPTY_OTP",
            "PHASE_STATUS": "WAITING_FOR_OTP",
        }

    db = SessionLocal()
    try:
        before_msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
        sub = await submit_rubika_login_code(
            db,
            PILOT,
            challenge_id=challenge_id,
            code=otp,
            provider=LiveRubikaLoginProvider(),
            prover=resolve_canonical_candidate_prover(),
            secret_store=secret_store,
        )
        # Discard plaintext OTP immediately
        otp = None
        del otp
        secret_store.clear()

        if not sub.ok:
            db.commit()
            return {
                "OTP_SUBMIT_PASS": False,
                "submit_code": sub.code,
                "submit_state": sub.state,
                "challenge_id": challenge_id,
                "PHASE_STATUS": "BLOCKED",
            }
        db.commit()

        # Clear handshake secrets from Redis
        r2 = _redis()
        try:
            r2.delete(SECRET_KEY)
        finally:
            r2.close()

        sess_n = db.query(ChannelSession).filter(ChannelSession.account_id == PILOT).count()
        active_ids = list(
            db.execute(
                text(
                    "SELECT id FROM channel_sessions WHERE account_id=:a "
                    "AND session_status::text='active' ORDER BY id"
                ),
                {"a": PILOT},
            ).scalars().all()
        )
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
        ch = db.query(RubikaLoginChallenge).filter(RubikaLoginChallenge.id == challenge_id).first()
        sid = int(sub.active_session_id) if sub.active_session_id else (
            int(active_ids[0]) if active_ids else None
        )
        ok = (
            sess_n == 1
            and len(active_ids) == 1
            and sid == int(active_ids[0])
            and global_active == 4
            and msg == before_msg
            and ch is not None
            and str(ch.state.value) == "ready"
        )
        return {
            "OTP_SUBMIT_PASS": ok,
            "OTP_REQUEST_PASS": True,
            "PILOT_ACTIVE_SESSION_ID": sid,
            "PILOT_ACTIVE_SESSION_COUNT": len(active_ids),
            "PILOT_SESSION_COUNT": sess_n,
            "challenge_final_state": ch.state.value if ch else None,
            "auth_ready": sub.auth_ready,
            "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
            "MessageAttempt_count": msg,
            "MESSAGE_SENT": False,
            "PHASE_STATUS": "OTP_SUBMITTED" if ok else "BLOCKED",
        }
    finally:
        db.close()
        if "otp" in locals():
            try:
                del otp
            except Exception:
                pass


def cmd_status() -> dict:
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.models import ChannelSession, RubikaLoginChallenge

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        sess_n = db.query(ChannelSession).filter(ChannelSession.account_id == PILOT).count()
        active_ids = list(
            db.execute(
                text(
                    "SELECT id FROM channel_sessions WHERE account_id=:a "
                    "AND session_status::text='active' ORDER BY id"
                ),
                {"a": PILOT},
            ).scalars().all()
        )
        active_ch = (
            db.query(RubikaLoginChallenge)
            .filter(
                RubikaLoginChallenge.account_id == PILOT,
                RubikaLoginChallenge.state.in_(list(ACTIVE_CH)),
            )
            .count()
        )
        global_active = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        )
        return {
            "PILOT_ACCOUNT_ID": PILOT,
            "PILOT_SESSION_COUNT": sess_n,
            "PILOT_ACTIVE_SESSION_IDS": [int(x) for x in active_ids],
            "active_challenge_count": active_ch,
            "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
        }
    finally:
        db.rollback()
        db.close()


def main() -> int:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "help").strip().lower()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if cmd == "request":
        art = asyncio.run(cmd_request())
        REQUEST_ARTIFACT.write_text(json.dumps(art, indent=2, default=str), encoding="utf-8")
        print(json.dumps(art, indent=2, default=str))
        return 0 if art.get("OTP_REQUEST_PASS") else 2
    if cmd == "submit":
        art = asyncio.run(cmd_submit())
        SUBMIT_ARTIFACT.write_text(json.dumps(art, indent=2, default=str), encoding="utf-8")
        print(json.dumps(art, indent=2, default=str))
        return 0 if art.get("OTP_SUBMIT_PASS") else 2
    if cmd == "status":
        print(json.dumps(cmd_status(), indent=2, default=str))
        return 0
    print("usage: request | submit | status")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
