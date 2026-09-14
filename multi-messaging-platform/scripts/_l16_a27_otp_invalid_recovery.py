#!/usr/bin/env python3
"""L16 Account27 OTP_INVALID recovery — READ-ONLY forensic + classification.

Does NOT request OTP, submit OTP, delete challenges, or mutate sessions.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PILOT = 27
ACTIVE_CH = (
    "otp_requested",
    "otp_waiting_for_operator",
    "otp_submitted",
    "authenticating",
    "identity_verifying",
    "session_persisting",
)
TERMINAL = frozenset(
    {
        "login_failed",
        "login_expired",
        "ready",
        "manual_review_required",
    }
)
REPORT = Path("/app/reports/rubika-remediation/L16_A27_OTP_INVALID_RECOVERY.json")


def main() -> int:
    from sqlalchemy import text

    from core_engine.config import get_settings
    from core_engine.database import SessionLocal
    from core_engine.models import Account, ChannelSession, RubikaLoginChallenge
    from core_engine.services.rubika_login_state_machine import (
        ACTIVE_CHALLENGE_STATES,
        account_uses_l3_login,
    )
    from workers.config import get_worker_settings
    from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings
    import redis

    get_settings.cache_clear()
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        acct = db.query(Account).filter(Account.id == PILOT).first()
        sess_n = db.query(ChannelSession).filter(ChannelSession.account_id == PILOT).count()
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
        validating = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions WHERE account_id=:a "
                    "AND session_status::text='validating'"
                ),
                {"a": PILOT},
            ).scalar()
            or 0
        )
        sess_statuses = db.execute(
            text(
                "SELECT string_agg(id::text || ':' || session_status::text, ',' ORDER BY id) "
                "FROM channel_sessions WHERE account_id=:a"
            ),
            {"a": PILOT},
        ).scalar()
        ch_all = (
            db.query(RubikaLoginChallenge)
            .filter(RubikaLoginChallenge.account_id == PILOT)
            .order_by(RubikaLoginChallenge.created_at.desc())
            .all()
        )
        active_ch = [
            c
            for c in ch_all
            if (c.state.value if hasattr(c.state, "value") else str(c.state)) in ACTIVE_CH
        ]
        latest = ch_all[0] if ch_all else None
        now = datetime.now(timezone.utc)
        latest_state = None
        latest_id = None
        latest_expired = False
        failure_code = None
        attempt_count = None
        candidate_session_id = None
        completed_session_id = None
        expires_at = None
        if latest is not None:
            latest_id = latest.id
            latest_state = latest.state.value if hasattr(latest.state, "value") else str(latest.state)
            failure_code = latest.failure_code
            attempt_count = int(latest.attempt_count or 0)
            candidate_session_id = latest.candidate_session_id
            completed_session_id = latest.completed_session_id
            expires_at = latest.expires_at.isoformat() if latest.expires_at else None
            if latest.expires_at:
                exp = latest.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                latest_expired = exp <= now

        guid = (acct.rubika_guid or "").strip() if acct else ""
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

        # Source semantics: LOGIN_FAILED is NOT in ACTIVE_CHALLENGE_STATES
        active_enum_values = sorted(s.value for s in ACTIVE_CHALLENGE_STATES)
        login_failed_in_active = "login_failed" in active_enum_values

        # Classification from L3 semantics + observed state
        if latest_state == "login_failed" and (failure_code or "").upper() in {
            "OTP_INVALID",
            "",
        }:
            # Provider rejected code → terminal LOGIN_FAILED (no retry on same challenge)
            classification = "CHALLENGE_TERMINAL_AFTER_INVALID"
            reusable = False
            retry_allowed_on_same = False
        elif latest_state == "login_expired" or (
            latest_state in ACTIVE_CH and latest_expired
        ):
            classification = "CHALLENGE_EXPIRED"
            reusable = False
            retry_allowed_on_same = False
        elif latest_state == "ready":
            classification = "CHALLENGE_CONSUMED"
            reusable = False
            retry_allowed_on_same = False
        elif latest_state == "otp_waiting_for_operator" and not latest_expired:
            classification = "INVALID_CODE_RETRYABLE"
            reusable = True
            retry_allowed_on_same = True
        else:
            classification = "OTHER"
            reusable = latest_state in ACTIVE_CH and not latest_expired
            retry_allowed_on_same = reusable

        # Fresh request safety: no session, no active challenge, L3 on, no orphan candidate
        l3 = account_uses_l3_login(PILOT)
    finally:
        db.rollback()
        db.close()

    s = get_worker_settings()
    r = redis.from_url(s.REDIS_URL, decode_responses=True)
    try:
        q = int(r.llen(f"queue:rubika:{PILOT}"))
        cov = bool(r.exists(f"worker:coverage:rubika:{PILOT}")) or bool(
            r.exists(f"mmp:worker:account_coverage:rubika:{PILOT}")
        )
        # Also check canonical redis key helper if available
        try:
            from workers.redis_keys import worker_account_coverage_key

            cov = bool(r.exists(worker_account_coverage_key("rubika", PILOT)))
        except Exception:
            pass
    finally:
        r.close()

    try:
        ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
    except Exception:
        ids, meta = [], {}

    in_cohort = PILOT in (meta.get("cohort_ids") or [])
    allow = [
        int(x)
        for x in (s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS or "").split(",")
        if x.strip().isdigit()
    ]
    in_enforce = PILOT in allow

    partial_session = sess_n > 0 or validating > 0 or candidate_session_id is not None
    partial_identity = bool(guid)
    partial_worker = cov or (PILOT in ids) or in_cohort

    safe_fresh = (
        sess_n == 0
        and active_sess == 0
        and validating == 0
        and len(active_ch) == 0
        and q == 0
        and not guid
        and not cov
        and not in_cohort
        and not in_enforce
        and l3
        and not reusable
        and latest_state in TERMINAL
        and candidate_session_id is None
        and completed_session_id is None
    )

    artifact = {
        "PILOT_ACCOUNT_ID": PILOT,
        "ACCOUNT27_SESSION_COUNT": sess_n,
        "ACCOUNT27_ACTIVE_SESSION_COUNT": active_sess,
        "ACCOUNT27_VALIDATING_SESSION_COUNT": validating,
        "ACCOUNT27_SESSION_STATUSES": sess_statuses,
        "ACCOUNT27_LOGIN_CHALLENGE_COUNT": len(ch_all),
        "ACCOUNT27_ACTIVE_LOGIN_CHALLENGE_COUNT": len(active_ch),
        "LATEST_CHALLENGE_ID": latest_id,
        "LATEST_CHALLENGE_STATE": latest_state,
        "LATEST_CHALLENGE_FAILURE_CODE": failure_code,
        "LATEST_CHALLENGE_ATTEMPT_COUNT": attempt_count,
        "LATEST_CHALLENGE_EXPIRED": latest_expired,
        "LATEST_CHALLENGE_EXPIRES_AT": expires_at,
        "LATEST_CHALLENGE_RETRY_ALLOWED": retry_allowed_on_same,
        "LATEST_CANDIDATE_SESSION_ID": candidate_session_id,
        "LATEST_COMPLETED_SESSION_ID": completed_session_id,
        "CURRENT_CHALLENGE_REUSABLE": reusable,
        "OTP_FAILURE_CLASSIFICATION": classification,
        "L3_ACTIVE_STATES": active_enum_values,
        "L3_LOGIN_FAILED_IS_ACTIVE": login_failed_in_active,
        "OTP_INVALID_LEFT_PARTIAL_SESSION": partial_session,
        "OTP_INVALID_LEFT_PARTIAL_IDENTITY": partial_identity,
        "OTP_INVALID_LEFT_PARTIAL_WORKER": partial_worker,
        "PILOT_IDENTITY_GUID_BOUND": bool(guid),
        "PILOT_QUEUE": q,
        "PILOT_WORKER_COVERAGE": cov,
        "PILOT_IN_DISCOVERY_COHORT": in_cohort,
        "PILOT_IN_CANONICAL_ALLOWLIST": in_enforce,
        "PILOT_USES_L3_LOGIN_STATE_MACHINE": l3,
        "ACTUAL_WORKER_IDS": ids,
        "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
        "MessageAttempt_count": msg,
        "SAFE_TO_REQUEST_FRESH_ACCOUNT27_OTP": safe_fresh,
        "OTP_REQUESTED_AGAIN": False,
        "MESSAGE_SENT": False,
        "fresh_request_semantics": (
            "request_rubika_login ignores login_failed (not in ACTIVE_CHALLENGE_STATES); "
            "creates a new challenge after cooldown; preserves prior forensic row"
        ),
        "CURRENT_PHASE": "L16_ACCOUNT27_OTP_INVALID_RECOVERY",
        "PHASE_STATUS": (
            "READY_FOR_FRESH_OTP"
            if safe_fresh and not partial_session and not partial_identity and not partial_worker
            else "BLOCKED"
        ),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    print(json.dumps(artifact, indent=2, default=str))
    return 0 if artifact["PHASE_STATUS"] == "READY_FOR_FRESH_OTP" else 2


if __name__ == "__main__":
    raise SystemExit(main())
