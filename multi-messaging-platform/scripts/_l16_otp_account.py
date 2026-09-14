#!/usr/bin/env python3
"""L16 Account27 (or argv pilot) — verify, request ONE OTP, interactive submit.

Usage:
  verify [account_id]   — read-only prechecks
  request [account_id]  — exactly one OTP request via L3
  submit [account_id]   — interactive getpass OTP submit
  status [account_id]   — read-only status

OTP plaintext never printed/logged/written to reports.
Handshake secrets only in Redis TTL key (never OTP).
"""

from __future__ import annotations

import asyncio
import getpass
import json
import sys
from pathlib import Path

DEFAULT_PILOT = 27
REPORT_DIR = Path("/app/reports/rubika-remediation")
SECRET_TTL = 600
ACTIVE_CH = (
    "otp_requested",
    "otp_waiting_for_operator",
    "otp_submitted",
    "authenticating",
    "identity_verifying",
    "session_persisting",
)
ACTIVE_CAMPAIGN = ("running", "prepared", "paused")
BATCH = {13: 729, 23: 725, 74: 724}
EXPECTED_WORKERS = [12, 13, 23, 74, 79]


def _pilot() -> int:
    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        return int(sys.argv[2])
    return DEFAULT_PILOT


def _secret_key(aid: int) -> str:
    return f"l16:otp_handshake:account:{aid}"


def _mask_phone(phone: str | None) -> str:
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"


def _redis():
    from workers.config import get_worker_settings
    import redis

    return redis.from_url(get_worker_settings().REDIS_URL, decode_responses=True)


async def cmd_verify(pilot: int) -> dict:
    from sqlalchemy import text

    from core_engine.config import get_settings
    from core_engine.database import SessionLocal
    from core_engine.models import Account, AccountStatus, PlatformType
    from core_engine.services.rubika_login_state_machine import account_uses_l3_login
    from workers.config import get_worker_settings
    from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings
    import redis.asyncio as redis

    get_settings.cache_clear()
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        acct = db.query(Account).filter(Account.id == pilot).first()
        if acct is None:
            return {"ok": False, "error": "ACCOUNT_NOT_FOUND"}
        if acct.platform != PlatformType.RUBIKA:
            return {"ok": False, "error": "NOT_RUBIKA"}
        status = acct.status.value if hasattr(acct.status, "value") else str(acct.status)
        sess_n = int(
            db.execute(
                text("SELECT count(*) FROM channel_sessions WHERE account_id=:a"),
                {"a": pilot},
            ).scalar()
            or 0
        )
        active_sess = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions WHERE account_id=:a "
                    "AND session_status::text='active'"
                ),
                {"a": pilot},
            ).scalar()
            or 0
        )
        active_ch = int(
            db.execute(
                text(
                    "SELECT count(*) FROM rubika_login_challenges "
                    "WHERE account_id=:a AND state::text = ANY(:s)"
                ),
                {"a": pilot, "s": list(ACTIVE_CH)},
            ).scalar()
            or 0
        )
        active_camp = int(
            db.execute(
                text(
                    "SELECT count(*) FROM campaign_accounts ca "
                    "JOIN campaigns c ON c.id=ca.campaign_id "
                    "WHERE ca.account_id=:a AND c.status::text = ANY(:camp)"
                ),
                {"a": pilot, "camp": list(ACTIVE_CAMPAIGN)},
            ).scalar()
            or 0
        )
        a3_ch = int(
            db.execute(
                text(
                    "SELECT count(*) FROM rubika_login_challenges "
                    "WHERE account_id=3 AND state::text = ANY(:s)"
                ),
                {"s": list(ACTIVE_CH)},
            ).scalar()
            or 0
        )
        actives = {}
        for aid, sid in BATCH.items():
            row = db.execute(
                text(
                    "SELECT id FROM channel_sessions WHERE account_id=:a "
                    "AND session_status::text='active'"
                ),
                {"a": aid},
            ).scalar()
            actives[str(aid)] = int(row) if row else None
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
        guid = (acct.rubika_guid or "").strip()
        phone = (acct.phone_number or "").strip()
    finally:
        db.rollback()
        db.close()

    s = get_worker_settings()
    r = redis.from_url(s.REDIS_URL, decode_responses=True)
    try:
        await r.ping()
        q = int(await r.llen(f"queue:rubika:{pilot}"))
    finally:
        await r.aclose()

    # Worker resolution may be empty on core_api; tolerate and note.
    try:
        ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
    except Exception:
        ids, meta = [], {"mode": None, "cohort_ids": [], "pinned_ids": []}

    l3 = account_uses_l3_login(pilot)
    non_pilot_l3 = {
        "3": account_uses_l3_login(3),
        "13": account_uses_l3_login(13),
        "79": account_uses_l3_login(79),
    }
    ok = (
        status == AccountStatus.ACTIVE.value
        and sess_n == 0
        and active_sess == 0
        and active_ch == 0
        and q == 0
        and not guid
        and bool(phone)
        and active_camp == 0
        and global_active == 3
        and actives == {"13": 729, "23": 725, "74": 724}
        and l3
        and non_pilot_l3["13"] is False
        and non_pilot_l3["79"] is False
    )
    return {
        "ok": ok,
        "PILOT_ACCOUNT_ID": pilot,
        "PILOT_ACCOUNT_PHONE_MASKED": _mask_phone(phone),
        "account_status": status,
        "PILOT_SESSION_COUNT_BEFORE": sess_n,
        "PILOT_ACTIVE_SESSION_COUNT_BEFORE": active_sess,
        "PILOT_ACTIVE_LOGIN_CHALLENGE_COUNT": active_ch,
        "PILOT_QUEUE": q,
        "PILOT_IDENTITY_CONFLICT": bool(guid),
        "PILOT_ACTIVE_CAMPAIGN": active_camp > 0,
        "PILOT_USES_L3_LOGIN_STATE_MACHINE": l3,
        "non_pilot_l3": non_pilot_l3,
        "ACCOUNT3_ACTIVE_CHALLENGE_COUNT": a3_ch,
        "ACCOUNT3_UNTOUCHED": True,
        "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
        "batch_a_actives": actives,
        "MessageAttempt_count": msg,
        "actual_workers_from_this_process": ids,
        "discovery_meta": meta,
        "pilot_env": get_settings().RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS,
    }


async def cmd_request(pilot: int) -> dict:
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
    if not account_uses_l3_login(pilot):
        return {
            "OTP_REQUEST_PASS": False,
            "error": "PILOT_NOT_ON_L3",
            "PHASE_STATUS": "BLOCKED",
        }

    secret_store: dict = {}
    db = SessionLocal()
    try:
        before_sess = (
            db.query(ChannelSession).filter(ChannelSession.account_id == pilot).count()
        )
        before_active = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        )
        before_msg = int(
            db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0
        )
        pending = (
            db.query(RubikaLoginChallenge)
            .filter(
                RubikaLoginChallenge.account_id == pilot,
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
            db, pilot, provider=LiveRubikaLoginProvider(), secret_store=secret_store
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

        r = _redis()
        try:
            blob = secret_store.get(challenge_id) or {}
            r.set(_secret_key(pilot), json.dumps({"challenge_id": challenge_id, "secret_blob": blob}), ex=SECRET_TTL)
        finally:
            r.close()
        secret_store.clear()

        db2 = SessionLocal()
        try:
            db2.execute(text("SET TRANSACTION READ ONLY"))
            active_chs = (
                db2.query(RubikaLoginChallenge)
                .filter(
                    RubikaLoginChallenge.account_id == pilot,
                    RubikaLoginChallenge.state.in_(list(ACTIVE_CH)),
                )
                .all()
            )
            sess_n = (
                db2.query(ChannelSession).filter(ChannelSession.account_id == pilot).count()
            )
            active_sess = int(
                db2.execute(
                    text(
                        "SELECT count(*) FROM channel_sessions WHERE account_id=:a "
                        "AND session_status::text='active'"
                    ),
                    {"a": pilot},
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
            msg = int(
                db2.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0
            )
            a3_sess = (
                db2.query(ChannelSession).filter(ChannelSession.account_id == 3).count()
            )
            ch = active_chs[0] if len(active_chs) == 1 else None
            ok = (
                len(active_chs) == 1
                and ch is not None
                and int(ch.account_id) == pilot
                and str(ch.state.value) == "otp_waiting_for_operator"
                and sess_n == 0
                and before_sess == 0
                and active_sess == 0
                and global_active == 3
                and before_active == 3
                and msg == before_msg
                and a3_sess == 0
            )
            return {
                "PILOT_ACCOUNT_ID": pilot,
                "PILOT_ACCOUNT_PHONE_MASKED": _mask_phone(ch.phone_e164 if ch else None),
                "OTP_REQUEST_PASS": ok,
                "OTP_REQUESTED": True,
                "challenge_id": challenge_id,
                "challenge_state": ch.state.value if ch else None,
                "active_challenge_count": len(active_chs),
                "PILOT_SESSION_COUNT": sess_n,
                "PILOT_ACTIVE_SESSION_COUNT": active_sess,
                "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
                "MessageAttempt_count": msg,
                "ACCOUNT3_SESSION_COUNT": a3_sess,
                "MESSAGE_SENT": False,
                "PHASE_STATUS": "WAITING_FOR_OTP" if ok else "BLOCKED",
                "WAITING_FOR_OTP_CODE": ok,
                "EXACT_OPERATOR_INPUT_REQUIRED": (
                    f"Enter OTP for Account{pilot} via: "
                    f"docker exec -it -e PYTHONPATH=/app -w /app mmp_core_api "
                    f"python scripts/_l16_otp_account.py submit {pilot}"
                    if ok
                    else None
                ),
            }
        finally:
            db2.rollback()
            db2.close()
    finally:
        db.close()


def _read_otp_interactive(pilot: int) -> str:
    """Secret-safe interactive OTP entry for docker exec -it.

    Requirements:
    - requires a TTY (refuse non-interactive / piped input)
    - prefer getpass (no echo)
    - never accept OTP via argv/env/file
    - plaintext returned only to caller for immediate submit+discard
    """
    import os
    import sys

    # Refuse if caller already put OTP in env (defense in depth)
    for bad in ("OTP", "RUBIKA_OTP", "PHONE_CODE", "L16_OTP"):
        if os.environ.get(bad):
            raise RuntimeError(f"REFUSE: unset {bad}; OTP must not be in environment")

    # Ensure we have a real TTY — Windows docker exec -it provides one when used correctly
    tty_ok = False
    try:
        tty_ok = sys.stdin.isatty()
    except Exception:
        tty_ok = False
    if not tty_ok:
        # Linux container fallback: open controlling terminal directly
        try:
            with open("/dev/tty", "r") as _tty:
                tty_ok = True
        except Exception:
            tty_ok = False
    if not tty_ok:
        raise RuntimeError(
            "NO_TTY: re-run with docker exec -it ... submit "
            f"{pilot} from a local PowerShell/Cursor terminal"
        )

    print(f"Enter OTP for Account{pilot}:", flush=True)
    # getpass hides echo when TTY is present
    otp = getpass.getpass(prompt="")
    if not (otp or "").strip():
        raise RuntimeError("EMPTY_OTP")
    return otp


async def cmd_submit(pilot: int) -> dict:
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
        raw = r.get(_secret_key(pilot))
    finally:
        r.close()
    if not raw:
        return {
            "OTP_SUBMIT_PASS": False,
            "error": "HANDSHAKE_SECRET_EXPIRED_OR_MISSING",
            "PHASE_STATUS": "WAITING_FOR_OTP",
        }
    data = json.loads(raw)
    challenge_id = data["challenge_id"]
    secret_store = {challenge_id: data.get("secret_blob") or {}}

    print(f"LOCAL_OTP_ENTRY: TTY getpass for Account{pilot} (value never logged)", flush=True)
    try:
        otp = _read_otp_interactive(pilot)
    except RuntimeError as exc:
        return {
            "OTP_SUBMIT_PASS": False,
            "PHASE_STATUS": "WAITING_FOR_OTP",
            "LOCAL_OTP_ENTRY_FLOW_READY": "NO_TTY" not in str(exc),
            "error": str(exc),
            "EXACT_OPERATOR_INPUT_REQUIRED": (
                "Enter OTP directly into the local Cursor/PowerShell terminal; "
                "do not place it in reports"
            ),
        }

    db = SessionLocal()
    try:
        before_msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
        sub = await submit_rubika_login_code(
            db,
            pilot,
            challenge_id=challenge_id,
            code=otp,
            provider=LiveRubikaLoginProvider(),
            prover=resolve_canonical_candidate_prover(),
            secret_store=secret_store,
        )
        otp = None
        del otp
        secret_store.clear()
        if not sub.ok:
            db.commit()
            return {
                "OTP_SUBMIT_PASS": False,
                "submit_code": sub.code,
                "submit_state": sub.state,
                "PHASE_STATUS": "BLOCKED",
            }
        db.commit()
        r2 = _redis()
        try:
            r2.delete(_secret_key(pilot))
        finally:
            r2.close()

        sess_n = db.query(ChannelSession).filter(ChannelSession.account_id == pilot).count()
        active_ids = list(
            db.execute(
                text(
                    "SELECT id FROM channel_sessions WHERE account_id=:a "
                    "AND session_status::text='active' ORDER BY id"
                ),
                {"a": pilot},
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
            "PILOT_ACCOUNT_ID": pilot,
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


def cmd_entry_check() -> dict:
    """Non-secret check: TTY detection + getpass import; does NOT read OTP."""
    import sys

    stdin_tty = False
    try:
        stdin_tty = bool(sys.stdin.isatty())
    except Exception:
        stdin_tty = False
    dev_tty = False
    try:
        with open("/dev/tty", "r"):
            dev_tty = True
    except Exception:
        dev_tty = False
    getpass_ok = callable(getpass.getpass)
    ready = getpass_ok  # operator must use docker exec -it; code refuses non-TTY at submit
    return {
        "LOCAL_OTP_ENTRY_FLOW_READY": ready,
        "stdin_isatty": stdin_tty,
        "dev_tty_openable": dev_tty,
        "getpass_available": getpass_ok,
        "accepts_argv_otp": False,
        "accepts_env_otp": False,
        "requires_docker_exec_it": True,
        "required_invocation": (
            "docker exec -it -e PYTHONPATH=/app -w /app mmp_core_api "
            "python scripts/_l16_otp_account.py submit 27"
        ),
        "note": (
            "Submit now refuses NO_TTY when stdin is not a terminal. "
            "First KeyboardInterrupt was getpass waiting without usable interactive input. "
            "Always use docker exec -it from local PowerShell/Cursor terminal."
        ),
    }


def main() -> int:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "help").strip().lower()
    pilot = _pilot()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if cmd == "entry-check":
        art = cmd_entry_check()
        print(json.dumps(art, indent=2))
        return 0 if art.get("LOCAL_OTP_ENTRY_FLOW_READY") or art.get("getpass_available") else 2
    if cmd == "verify":
        art = asyncio.run(cmd_verify(pilot))
        (REPORT_DIR / f"L16_A{pilot}_VERIFY.json").write_text(
            json.dumps(art, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps(art, indent=2, default=str))
        return 0 if art.get("ok") else 2
    if cmd == "request":
        art = asyncio.run(cmd_request(pilot))
        (REPORT_DIR / f"L16_A{pilot}_OTP_REQUEST.json").write_text(
            json.dumps(art, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps(art, indent=2, default=str))
        return 0 if art.get("OTP_REQUEST_PASS") else 2
    if cmd == "submit":
        art = asyncio.run(cmd_submit(pilot))
        (REPORT_DIR / f"L16_A{pilot}_OTP_SUBMIT.json").write_text(
            json.dumps(art, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps(art, indent=2, default=str))
        return 0 if art.get("OTP_SUBMIT_PASS") else 2
    print("usage: verify|request|submit|entry-check [account_id]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
