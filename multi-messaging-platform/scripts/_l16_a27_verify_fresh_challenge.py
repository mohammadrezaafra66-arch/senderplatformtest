#!/usr/bin/env python3
"""Read-only verify fresh Account27 OTP challenge vs previous login_failed."""
import json
from sqlalchemy import text
from core_engine.database import SessionLocal
from core_engine.models import ChannelSession, RubikaLoginChallenge

PILOT = 27
PREV = "a199bdc4e4024f8380a6dba68be1b079"
NEW = "2740190fa81743bdbbb7695c5537348b"
ACTIVE = (
    "otp_requested",
    "otp_waiting_for_operator",
    "otp_submitted",
    "authenticating",
    "identity_verifying",
    "session_persisting",
)

db = SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    chs = (
        db.query(RubikaLoginChallenge)
        .filter(RubikaLoginChallenge.account_id == PILOT)
        .order_by(RubikaLoginChallenge.created_at.desc())
        .all()
    )
    prev = next((c for c in chs if c.id == PREV), None)
    new = next((c for c in chs if c.id == NEW), None)
    active = [c for c in chs if c.state.value in ACTIVE]
    sess = db.query(ChannelSession).filter(ChannelSession.account_id == PILOT).count()
    active_sess = int(
        db.execute(
            text(
                "SELECT count(*) FROM channel_sessions WHERE account_id=:a "
                "AND session_status::text='active'"
            ),
            {"a": PILOT},
        ).scalar()
        or 0
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
    guid = (
        db.execute(text("SELECT rubika_guid FROM accounts WHERE id=:a"), {"a": PILOT}).scalar()
        or ""
    )
    msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
    ok = (
        prev is not None
        and new is not None
        and new.id != prev.id
        and len(active) == 1
        and active[0].id == NEW
        and new.state.value == "otp_waiting_for_operator"
        and prev.state.value == "login_failed"
        and sess == 0
        and active_sess == 0
        and not str(guid).strip()
        and global_active == 3
        and msg == 5
    )
    print(
        json.dumps(
            {
                "FRESH_OTP_REQUEST_PASS": ok,
                "PREVIOUS_CHALLENGE_ID": PREV,
                "PREVIOUS_CHALLENGE_STATE": prev.state.value if prev else None,
                "NEW_CHALLENGE_ID": NEW,
                "NEW_CHALLENGE_STATE": new.state.value if new else None,
                "ids_differ": NEW != PREV,
                "active_challenge_count": len(active),
                "total_challenges": len(chs),
                "ACCOUNT27_SESSION_COUNT": sess,
                "ACCOUNT27_ACTIVE_SESSION_COUNT": active_sess,
                "identity_bound": bool(str(guid).strip()),
                "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
                "MessageAttempt_count": msg,
                "MESSAGE_SENT": False,
            },
            indent=2,
        )
    )
finally:
    db.rollback()
    db.close()
