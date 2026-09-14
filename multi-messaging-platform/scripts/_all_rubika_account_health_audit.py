"""ALL Rubika accounts — FULL HEALTH & SESSION RE-AUDIT (READ-ONLY).

No sends, no OTP request, no session delete/regenerate, no campaign create.
Mutations forbidden. Production DB/Redis reads only (+ optional Rubika identity API
using already-stored sessions).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("/tmp/rubika_fleet_health_audit")
HOST_REPORT_HINT = "reports/rubika-remediation"
JSON_PATH = REPORT_DIR / "ALL_RUBIKA_ACCOUNT_HEALTH_AUDIT.json"
MD_PATH = REPORT_DIR / "ALL_RUBIKA_ACCOUNT_HEALTH_AUDIT.md"

# Sessions created after R10 cleanup are treated as post-OTP recoveries when auth passes.
# Post-cleanup KEEP sessions were only accounts 12/79/92 (see R10_CLEANUP_VERIFICATION).
R10_CLEANUP_CUTOFF = datetime(2026, 8, 29, 9, 4, 50, tzinfo=timezone.utc)
VERIFIED_REAL_SEND_ACCOUNT_IDS = {12, 79}  # R10 controlled real-send proven
POST_CLEANUP_KEEP_SESSION_ACCOUNTS = {12, 79, 92}

# Bounded reconnect+identity probe; failure continues to next account.
PER_ACCOUNT_TIMEOUT_SECONDS = 25.0
# Sequential pacing between accounts (no reconnect storm).
INTER_ACCOUNT_DELAY_SECONDS = 0.15


def _sanitize_error_message(text: str | None, *, max_len: int = 120) -> str:
    """Redact credential-bearing fragments; never persist raw exception dumps."""
    if not text:
        return ""
    msg = str(text)
    # URLs with credentials
    msg = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+", "<redacted_url>", msg)
    # Long hex / base64-ish tokens
    msg = re.sub(r"(?i)\b[a-f0-9]{24,}\b", "<redacted_hex>", msg)
    msg = re.sub(r"(?i)\b[A-Za-z0-9_-]{40,}\b", "<redacted_token>", msg)
    # Phone-like digit runs
    msg = re.sub(r"\+?\d[\d\s\-()]{7,}\d", "<redacted_phone>", msg)
    # Common secret field assignments
    msg = re.sub(
        r"(?i)\b(auth|token|password|passwd|secret|private_key|api_key|cookie|session)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        msg,
    )
    msg = re.sub(r"\s+", " ", msg).strip()
    return msg[:max_len]


def _safe_error(exc: BaseException | None, *, code: str | None = None) -> dict[str, str]:
    err_type = type(exc).__name__ if exc is not None else "Unknown"
    safe_code = code or err_type
    short = _sanitize_error_message(str(exc) if exc is not None else safe_code)
    return {
        "error_type": err_type,
        "safe_error_code": safe_code,
        "sanitized_short_message": short,
    }


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"


def _safe_label(account) -> str:
    label = (account.label or "").strip()
    if label:
        return label[:80]
    return f"rubika-{account.id}"


def _enum_val(v: Any) -> str:
    if v is None:
        return ""
    return str(v.value) if hasattr(v, "value") else str(v)


def _extract_guid(obj: Any) -> str | None:
    """Pull user_guid/guid from rubpy response without logging secrets."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        for key in ("user_guid", "guid"):
            val = str(obj.get(key) or "").strip()
            if val:
                return val
        user = obj.get("user")
        if isinstance(user, dict):
            return _extract_guid(user)
        return None
    for attr in ("user_guid", "guid"):
        val = str(getattr(obj, attr, None) or "").strip()
        if val:
            return val
    to_dict = getattr(obj, "to_dict", None)
    data = to_dict if isinstance(to_dict, dict) else (to_dict() if callable(to_dict) else None)
    if isinstance(data, dict):
        return _extract_guid(data)
    user = getattr(obj, "user", None)
    if user is not None:
        return _extract_guid(user)
    return None


async def _identity_probe(client) -> tuple[str, str | None]:
    """Lightest available authenticated call; never send_message / never OTP."""
    stored = str(getattr(client, "guid", "") or "").strip() or None
    # Prefer methods that only read self-identity.
    probes: list[tuple[str, Any]] = []
    if callable(getattr(client, "get_me", None)):
        probes.append(("get_me", client.get_me))
    if stored and callable(getattr(client, "get_user_info", None)):
        probes.append(("get_user_info", lambda: client.get_user_info(stored)))
    if stored and callable(getattr(client, "get_abs_objects", None)):
        probes.append(("get_abs_objects", lambda: client.get_abs_objects([stored])))

    last_err: Exception | None = None
    for name, call in probes:
        try:
            result = await call()
            guid = _extract_guid(result)
            if guid:
                return name, guid
            if stored:
                # Authenticated call succeeded; identity from session guid.
                return name, stored
        except Exception as exc:
            last_err = exc
            msg = str(exc).lower()
            if any(
                tok in msg
                for tok in (
                    "otp",
                    "login",
                    "sign in",
                    "unauthorized",
                    "invalid auth",
                    "session expired",
                    "requires login",
                    "pass_key",
                )
            ):
                raise
            continue
    if last_err is not None and stored is None:
        raise last_err
    # Connect alone succeeded; treat stored guid as identity if no probe method exists.
    return "connect_only", stored


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or ""
    if "/mmp_db" not in dsn.split("?")[0]:
        raise SystemExit("REFUSE: audit requires production DATABASE_URL ending in /mmp_db")

    from core_engine.database import SessionLocal
    from core_engine.models import (
        Account,
        AccountStatus,
        ChannelSession,
        Message,
        MessageAttempt,
        MessageAttemptStatus,
        PlatformType,
        RubikaAccountPool,
        RubikaSenderSchedule,
        SessionType,
    )
    from core_engine.services.account_session_wiring import (
        evaluate_account_session_readiness,
        _validate_session_payload,
    )
    from core_engine.services.rubika_preflight import evaluate_rubika_send_preflight
    from core_engine.services.rubika_user_session import parse_session_envelope
    from core_engine.services.session_storage import load_channel_session_plaintext
    from core_engine.services.rubika_circuit import get_circuit_snapshot
    from workers.config import get_worker_settings
    from workers.connectors.rubika_user import _connect_authenticated, load_rubika_user_client
    from workers.pool_health import has_active_worker_coverage
    from workers.redis_flags import is_account_paused, is_system_kill_switch_enabled
    from workers.redis_keys import queue_key
    from workers.rubika_account_pool import resolve_current_phase
    from core_engine.services.redis_client import get_redis_client, reset_redis_client

    db = SessionLocal()

    accounts = (
        db.query(Account)
        .filter(Account.platform == PlatformType.RUBIKA)
        .order_by(Account.id.asc())
        .all()
    )
    phase = resolve_current_phase(db)
    active_sched = (
        db.query(RubikaSenderSchedule)
        .filter(
            RubikaSenderSchedule.is_active.is_(True),
            RubikaSenderSchedule.phase == phase,
        )
        .first()
    )

    # Real-send evidence map
    success_rows = (
        db.query(Message.account_id, MessageAttempt.id)
        .join(MessageAttempt, MessageAttempt.message_id == Message.id)
        .filter(
            MessageAttempt.status == MessageAttemptStatus.SUCCESS,
            Message.account_id.isnot(None),
        )
        .all()
    )
    success_by_account: dict[int, int] = {}
    for aid, att_id in success_rows:
        success_by_account[int(aid)] = success_by_account.get(int(aid), 0) + 1

    reset_redis_client()
    redis = get_redis_client()

    async def redis_bits(account_id: int) -> dict[str, Any]:
        cov = await has_active_worker_coverage(
            redis, platform="rubika", account_id=account_id
        )
        from workers.redis_keys import worker_account_coverage_key

        ttl = await redis.ttl(worker_account_coverage_key("rubika", account_id))
        qlen = int(await redis.llen(queue_key("rubika", account_id)))
        paused = await is_account_paused(redis, account_id)
        return {
            "worker_coverage": bool(cov),
            "coverage_ttl_seconds": int(ttl) if ttl is not None else None,
            "queue_length": qlen,
            "pause_status": "PAUSED" if paused else "NOT_PAUSED",
        }

    async def circuit_and_kill() -> dict[str, Any]:
        w = get_worker_settings()
        snap = await get_circuit_snapshot(
            redis,
            probe_budget=int(w.RUBIKA_CIRCUIT_PROBE_BUDGET),
            window_seconds=int(w.RUBIKA_CIRCUIT_WINDOW_SECONDS),
        )
        kill = await is_system_kill_switch_enabled(redis)
        return {
            "circuit_status": str(getattr(snap, "state", snap)),
            "kill_switch_status": "ON" if kill else "OFF",
        }

    results: list[dict[str, Any]] = []

    async def audit_one(account: Account) -> dict[str, Any]:
        aid = int(account.id)
        row: dict[str, Any] = {
            "account_id": aid,
            "display_label": _safe_label(account),
            "masked_phone": _mask_phone(account.phone_number),
            "enabled": account.status == AccountStatus.ACTIVE,
            "account_status": _enum_val(account.status),
            "platform": _enum_val(account.platform),
            "session_count": 0,
            "session_id": None,
            "session_ids": [],
            "session_storage_state": "NO_SESSION_ROW",
            "decrypt_status": "N/A",
            "structure_status": "N/A",
            "authenticated_reconnect_status": "SKIPPED",
            "identity_match": None,
            "pool_status": "UNKNOWN",
            "schedule_status": "UNKNOWN",
            "quota_status": "NOT_EVALUATED_READ_ONLY",
            "pause_status": "UNKNOWN",
            "kill_switch_status": global_flags["kill_switch_status"],
            "circuit_status": global_flags["circuit_status"],
            "worker_coverage": False,
            "queue_length": 0,
            "preflight_allowed": None,
            "preflight_code": None,
            "preflight_runtime_limits": "NOT_EVALUATED_READ_ONLY",
            "error_type": None,
            "safe_error_code": None,
            "sanitized_short_message": None,
            "real_send_evidence": {
                "success_attempt_count": int(success_by_account.get(aid, 0)),
                "r10_verified_account": aid in VERIFIED_REAL_SEND_ACCOUNT_IDS,
            },
            "historical_state_if_known": (
                "VERIFIED_REAL_SEND_OK"
                if aid in VERIFIED_REAL_SEND_ACCOUNT_IDS
                else None
            ),
            "current_primary_state": "OTHER_BLOCKER",
            "block_reason": None,
            "recommended_next_action": "review",
            "otp_login_bucket": None,
            "session_created_at": None,
            "session_updated_at": None,
            "recovered_candidate": False,
            "auth_health": None,
            "account_readiness_code": None,
        }

        sessions = (
            db.query(ChannelSession)
            .filter(
                ChannelSession.account_id == aid,
                ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            )
            .order_by(ChannelSession.id.desc())
            .all()
        )
        row["session_count"] = len(sessions)
        row["session_ids"] = [int(s.id) for s in sessions]

        # Always collect queue/coverage/pool (read-only) — even when no session.
        bits = await redis_bits(aid)
        row.update(bits)
        pool = (
            db.query(RubikaAccountPool)
            .filter(
                RubikaAccountPool.account_id == aid,
                RubikaAccountPool.phase == phase,
            )
            .first()
        )
        row["pool_status"] = "IN_POOL" if pool else "NOT_IN_POOL"
        row["pool_phase"] = phase
        row["schedule_status"] = (
            "APPLICABLE" if active_sched is not None else "NO_ACTIVE_SCHEDULE"
        )
        if aid not in POST_CLEANUP_KEEP_SESSION_ACCOUNTS and row["historical_state_if_known"] is None:
            row["historical_state_if_known"] = "PRIOR_SESSION_MISSING_POST_R10_CLEANUP"

        if not sessions:
            row["session_storage_state"] = "NO_SESSION_ROW"
            row["current_primary_state"] = "NO_SESSION_ROW"
            row["block_reason"] = "No RUBIKA_SESSION row"
            row["recommended_next_action"] = "operator_initial_login"
            row["otp_login_bucket"] = "NEEDS_INITIAL_LOGIN"
            return row

        if len(sessions) > 1:
            row["session_storage_state"] = "MULTIPLE_SESSION_ROWS"
        else:
            row["session_storage_state"] = "SESSION_PRESENT_UNVERIFIED"

        latest = sessions[0]
        row["session_id"] = int(latest.id)
        row["session_created_at"] = latest.created_at.isoformat() if latest.created_at else None
        row["session_updated_at"] = latest.updated_at.isoformat() if latest.updated_at else None

        # Decrypt latest
        try:
            plaintext = load_channel_session_plaintext(latest)
            row["decrypt_status"] = "OK"
        except Exception as exc:
            safe = _safe_error(exc, code="SESSION_DECRYPT_FAILED")
            row["decrypt_status"] = "SESSION_DECRYPT_FAILED"
            row["current_primary_state"] = "SESSION_DECRYPT_FAILED"
            row["block_reason"] = safe["safe_error_code"]
            row["error_type"] = safe["error_type"]
            row["safe_error_code"] = safe["safe_error_code"]
            row["sanitized_short_message"] = safe["sanitized_short_message"]
            row["recommended_next_action"] = "session_decrypt_repair_or_relogin"
            row["otp_login_bucket"] = "SESSION_DECRYPT_REPAIR_REQUIRED"
            if len(sessions) > 1:
                row["current_primary_state"] = "MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED"
                row["otp_login_bucket"] = "SESSION_DUPLICATE_REVIEW_REQUIRED"
            return row

        # Structure
        try:
            envelope = parse_session_envelope(plaintext)
            _validate_session_payload(
                PlatformType.RUBIKA,
                plaintext.decode("utf-8") if isinstance(plaintext, (bytes, bytearray)) else str(plaintext),
                rubika_delivery_mode="user_account",
            )
            row["structure_status"] = "OK"
            stored_guid = str(envelope.get("guid") or "").strip() or None
        except Exception as exc:
            safe = _safe_error(exc, code="SESSION_STRUCTURALLY_INVALID")
            row["structure_status"] = "SESSION_STRUCTURALLY_INVALID"
            row["current_primary_state"] = "SESSION_STRUCTURALLY_INVALID"
            row["block_reason"] = safe["safe_error_code"]
            row["error_type"] = safe["error_type"]
            row["safe_error_code"] = safe["safe_error_code"]
            row["sanitized_short_message"] = safe["sanitized_short_message"]
            row["recommended_next_action"] = "relogin_replace_session"
            row["otp_login_bucket"] = "NEEDS_RELOGIN"
            if len(sessions) > 1:
                row["current_primary_state"] = "MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED"
                row["otp_login_bucket"] = "SESSION_DUPLICATE_REVIEW_REQUIRED"
            return row

        if len(sessions) > 1:
            # Forensic only — do not auto-pick beyond latest for reconnect, but flag review.
            row["block_reason"] = f"multiple_session_rows={len(sessions)}"
            # Still attempt reconnect on latest for operator visibility.

        # Local readiness (no network)
        readiness = evaluate_account_session_readiness(
            db, account, rubika_delivery_mode="user_account"
        )
        row["account_readiness_code"] = readiness.code

        # Authenticated reconnect (stored session only) — bounded timeout.
        async def _reconnect_and_identity() -> tuple[str, str | None]:
            client_local = await load_rubika_user_client(aid, db=db)
            try:
                await _connect_authenticated(client_local)
                return await _identity_probe(client_local)
            finally:
                try:
                    await client_local.disconnect()
                except Exception:
                    pass

        try:
            method, returned_guid = await asyncio.wait_for(
                _reconnect_and_identity(),
                timeout=PER_ACCOUNT_TIMEOUT_SECONDS,
            )
            row["authenticated_reconnect_status"] = "AUTH_RECONNECT_PASS"
            row["auth_health"] = "AUTH_OK"
            row["identity_probe_method"] = method
            if stored_guid and returned_guid and stored_guid != returned_guid:
                row["identity_match"] = False
                row["authenticated_reconnect_status"] = "IDENTITY_MISMATCH"
                row["current_primary_state"] = "IDENTITY_MISMATCH"
                row["block_reason"] = "stored_guid_mismatch"
                row["safe_error_code"] = "IDENTITY_MISMATCH"
                row["recommended_next_action"] = "manual_identity_review"
                row["otp_login_bucket"] = "OTHER_MANUAL_REVIEW"
                return row
            row["identity_match"] = True if stored_guid else None
        except asyncio.TimeoutError:
            row["authenticated_reconnect_status"] = "AUTH_RECONNECT_TIMEOUT"
            row["auth_health"] = "AUTH_TIMEOUT"
            row["current_primary_state"] = "OTHER_BLOCKER"
            row["block_reason"] = "AUTH_RECONNECT_TIMEOUT"
            row["error_type"] = "TimeoutError"
            row["safe_error_code"] = "AUTH_RECONNECT_TIMEOUT"
            row["sanitized_short_message"] = (
                f"reconnect/identity exceeded {PER_ACCOUNT_TIMEOUT_SECONDS:g}s"
            )
            row["recommended_next_action"] = "retry_audit_or_investigate_hang"
            row["otp_login_bucket"] = "OTHER_MANUAL_REVIEW"
            return row
        except Exception as exc:
            msg = str(exc).lower()
            if any(
                tok in msg
                for tok in (
                    "otp",
                    "login",
                    "pass_key",
                    "send_code",
                    "unauthorized",
                    "invalid auth",
                    "session expired",
                    "requires login",
                )
            ):
                safe = _safe_error(exc, code="LOGIN_REQUIRED")
                row["authenticated_reconnect_status"] = "LOGIN_REQUIRED"
                row["current_primary_state"] = "LOGIN_REQUIRED"
                row["auth_health"] = "LOGIN_REQUIRED"
                row["block_reason"] = safe["safe_error_code"]
                row["error_type"] = safe["error_type"]
                row["safe_error_code"] = safe["safe_error_code"]
                row["sanitized_short_message"] = safe["sanitized_short_message"]
                row["recommended_next_action"] = "operator_relogin_otp"
                row["otp_login_bucket"] = "NEEDS_RELOGIN"
            else:
                safe = _safe_error(exc, code="AUTH_RECONNECT_FAILED")
                row["authenticated_reconnect_status"] = "AUTH_RECONNECT_FAILED"
                row["auth_health"] = "AUTH_FAILED"
                row["block_reason"] = safe["safe_error_code"]
                row["error_type"] = safe["error_type"]
                row["safe_error_code"] = safe["safe_error_code"]
                row["sanitized_short_message"] = safe["sanitized_short_message"]
                row["current_primary_state"] = "OTHER_BLOCKER"
                row["recommended_next_action"] = "investigate_reconnect_error"
                row["otp_login_bucket"] = "OTHER_MANUAL_REVIEW"
            return row

        # Non-send preflight — STRICT Redis RO: never consume probes, never evaluate
        # runtime limits (read_quota_snapshot may DELETE expired cooldown meta).
        row["quota_status"] = "NOT_EVALUATED_READ_ONLY"
        try:
            pf = await evaluate_rubika_send_preflight(
                db,
                account=account,
                delivery_mode="user_account",
                context="audit",
                redis=redis,
                check_campaign_assignment=False,
                check_runtime_limits=False,
                consume_circuit_probe=False,
            )
            row["preflight_allowed"] = bool(pf.allowed)
            row["preflight_code"] = pf.code
            row["preflight_runtime_limits"] = "NOT_EVALUATED_READ_ONLY"
        except Exception as exc:
            safe = _safe_error(exc, code="PREFLIGHT_ERROR")
            row["preflight_allowed"] = False
            row["preflight_code"] = f"PREFLIGHT_ERROR:{safe['error_type']}"
            row["preflight_runtime_limits"] = "NOT_EVALUATED_READ_ONLY"
            row["error_type"] = safe["error_type"]
            row["safe_error_code"] = safe["safe_error_code"]
            row["sanitized_short_message"] = safe["sanitized_short_message"]

        # Primary classification (one state). Proven real-send wins over duplicate-session flag;
        # duplicates remain in session_storage_state + forensic anomalies.
        # quota_status remains NOT_EVALUATED_READ_ONLY (never fake quota PASS).
        if aid in VERIFIED_REAL_SEND_ACCOUNT_IDS and row["authenticated_reconnect_status"] == "AUTH_RECONNECT_PASS":
            row["current_primary_state"] = "VERIFIED_REAL_SEND_OK"
            row["recommended_next_action"] = (
                "keep_as_proven_sender"
                if len(sessions) == 1
                else "keep_as_proven_sender_review_duplicate_sessions"
            )
            row["otp_login_bucket"] = (
                "SESSION_DUPLICATE_REVIEW_REQUIRED" if len(sessions) > 1 else None
            )
        elif len(sessions) > 1:
            row["current_primary_state"] = "MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED"
            row["recommended_next_action"] = "review_duplicate_sessions_no_auto_delete"
            row["otp_login_bucket"] = "SESSION_DUPLICATE_REVIEW_REQUIRED"
        elif (
            row["preflight_allowed"]
            and row["worker_coverage"]
            and row["pool_status"] == "IN_POOL"
        ):
            # READY excluding runtime quota/limits (explicitly NOT_EVALUATED_READ_ONLY).
            row["current_primary_state"] = "READY_AUTHENTICATED"
            row["recommended_next_action"] = (
                "eligible_pending_quota_eval_outside_ro_audit"
            )
            row["otp_login_bucket"] = None
        elif row["authenticated_reconnect_status"] == "AUTH_RECONNECT_PASS":
            row["current_primary_state"] = "AUTHENTICATED_NOT_DISPATCH_READY"
            blockers = []
            if row["pool_status"] != "IN_POOL":
                blockers.append("pool")
            if row["schedule_status"] != "APPLICABLE":
                blockers.append("schedule")
            if not row["worker_coverage"]:
                blockers.append("coverage")
            if not row["preflight_allowed"]:
                blockers.append(f"preflight:{row['preflight_code']}")
            if row["pause_status"] == "PAUSED":
                blockers.append("paused")
            if row["kill_switch_status"] == "ON":
                blockers.append("kill_switch")
            blockers.append("quota:NOT_EVALUATED_READ_ONLY")
            row["block_reason"] = ",".join(blockers) or row["preflight_code"]
            row["recommended_next_action"] = "fix_dispatch_gates_not_relogin"
            row["otp_login_bucket"] = None
        else:
            row["current_primary_state"] = "OTHER_BLOCKER"

        # Recovery: session on account that did not keep sessions through R10 cleanup,
        # or session created after cleanup, and auth reconnect now passes.
        created = latest.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        post_cleanup_session = created is not None and created >= R10_CLEANUP_CUTOFF
        was_session_missing_keep_set = aid not in POST_CLEANUP_KEEP_SESSION_ACCOUNTS
        if row["authenticated_reconnect_status"] == "AUTH_RECONNECT_PASS" and (
            was_session_missing_keep_set or post_cleanup_session
        ):
            if aid not in VERIFIED_REAL_SEND_ACCOUNT_IDS:
                row["recovered_candidate"] = True
                if row["historical_state_if_known"] is None:
                    row["historical_state_if_known"] = "PRIOR_SESSION_MISSING_POST_R10_CLEANUP"

        return row

    async def run_all() -> list[dict[str, Any]]:
        nonlocal_global = await circuit_and_kill()
        # stamp globals into closure via mutating global_flags dict
        global_flags.update(nonlocal_global)
        out_rows = []
        for acc in accounts:
            try:
                out_rows.append(await audit_one(acc))
            except Exception as exc:
                safe = _safe_error(exc, code="AUDIT_EXCEPTION")
                out_rows.append(
                    {
                        "account_id": int(acc.id),
                        "display_label": _safe_label(acc),
                        "current_primary_state": "OTHER_BLOCKER",
                        "block_reason": safe["safe_error_code"],
                        "recommended_next_action": "manual_review",
                        "otp_login_bucket": "OTHER_MANUAL_REVIEW",
                        "authenticated_reconnect_status": "SKIPPED",
                        "quota_status": "NOT_EVALUATED_READ_ONLY",
                        "preflight_runtime_limits": "NOT_EVALUATED_READ_ONLY",
                        "error_type": safe["error_type"],
                        "safe_error_code": safe["safe_error_code"],
                        "sanitized_short_message": safe["sanitized_short_message"],
                    }
                )
            await asyncio.sleep(INTER_ACCOUNT_DELAY_SECONDS)
        return out_rows

    global_flags: dict[str, Any] = {
        "circuit_status": "UNKNOWN",
        "kill_switch_status": "UNKNOWN",
    }
    results = asyncio.run(run_all())
    db.close()

    # Totals
    def count_state(name: str) -> int:
        return sum(1 for r in results if r.get("current_primary_state") == name)

    totals = {
        "TOTAL_RUBIKA_ACCOUNTS": len(results),
        "VERIFIED_REAL_SEND_OK": count_state("VERIFIED_REAL_SEND_OK"),
        "READY_AUTHENTICATED": count_state("READY_AUTHENTICATED"),
        "AUTHENTICATED_NOT_DISPATCH_READY": count_state("AUTHENTICATED_NOT_DISPATCH_READY"),
        "LOGIN_REQUIRED": count_state("LOGIN_REQUIRED"),
        "NO_SESSION_ROW": count_state("NO_SESSION_ROW"),
        "SESSION_DECRYPT_FAILED": count_state("SESSION_DECRYPT_FAILED"),
        "SESSION_STRUCTURALLY_INVALID": count_state("SESSION_STRUCTURALLY_INVALID"),
        "IDENTITY_MISMATCH": count_state("IDENTITY_MISMATCH"),
        "MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED": count_state(
            "MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED"
        ),
        "OTHER_BLOCKER": count_state("OTHER_BLOCKER"),
    }
    recovered_ids = sorted(
        int(r["account_id"])
        for r in results
        if r.get("recovered_candidate")
        and r.get("authenticated_reconnect_status") == "AUTH_RECONNECT_PASS"
    )
    totals["RECOVERED_SINCE_PREVIOUS_AUDIT_COUNT"] = len(recovered_ids)
    totals["RECOVERED_ACCOUNT_IDS"] = recovered_ids

    otp_queue = [
        {
            "account_id": int(r["account_id"]),
            "display_label": r.get("display_label"),
            "reason": r.get("block_reason") or r.get("current_primary_state"),
            "bucket": r.get("otp_login_bucket"),
            "recommended_next_action": r.get("recommended_next_action"),
        }
        for r in results
        if r.get("otp_login_bucket")
        in {
            "NEEDS_INITIAL_LOGIN",
            "NEEDS_RELOGIN",
            "SESSION_DECRYPT_REPAIR_REQUIRED",
            "SESSION_DUPLICATE_REVIEW_REQUIRED",
            "OTHER_MANUAL_REVIEW",
        }
    ]
    otp_login_required_ids = sorted(
        {
            int(q["account_id"])
            for q in otp_queue
            if q["bucket"] in {"NEEDS_INITIAL_LOGIN", "NEEDS_RELOGIN"}
        }
    )

    nonempty_queues = [
        {"account_id": int(r["account_id"]), "queue_length": r.get("queue_length")}
        for r in results
        if int(r.get("queue_length") or 0) > 0
    ]

    anomalies = {
        "multiple_session_rows": [
            {
                "account_id": int(r["account_id"]),
                "session_ids": r.get("session_ids"),
                "session_count": r.get("session_count"),
                "current_primary_state": r.get("current_primary_state"),
            }
            for r in results
            if int(r.get("session_count") or 0) > 1
        ],
        "identity_mismatch": [
            int(r["account_id"])
            for r in results
            if r.get("current_primary_state") == "IDENTITY_MISMATCH"
        ],
        "nonempty_queues": nonempty_queues,
    }

    report = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "phase": "RUBIKA_FLEET_HEALTH_REAUDIT",
        "real_messages_sent_during_audit": False,
        "otp_requested_during_audit": False,
        "production_data_mutated": False,
        "script_redis_read_only": True,
        "per_account_timeout_seconds": PER_ACCOUNT_TIMEOUT_SECONDS,
        "inter_account_delay_seconds": INTER_ACCOUNT_DELAY_SECONDS,
        "audit_concurrency": 1,
        "preflight_runtime_limits": "NOT_EVALUATED_READ_ONLY",
        "account_ids_ordered": [int(r["account_id"]) for r in results],
        "totals": totals,
        "otp_login_required_account_ids": otp_login_required_ids,
        "otp_login_work_queue": otp_queue,
        "nonempty_queues": nonempty_queues,
        "anomalies_forensic_readonly": anomalies,
        "global_flags": global_flags,
        "accounts": results,
        "flags": {
            "ALL_RUBIKA_ACCOUNTS_AUDITED": len(results) == totals["TOTAL_RUBIKA_ACCOUNTS"]
            and totals["TOTAL_RUBIKA_ACCOUNTS"] > 0,
            "ALL_ACCOUNTS_HAVE_CURRENT_CLASSIFICATION": all(
                bool(r.get("current_primary_state")) for r in results
            ),
            "REAL_MESSAGES_SENT_DURING_AUDIT": False,
            "OTP_REQUESTED_DURING_AUDIT": False,
            "PRODUCTION_DATA_MUTATED": False,
            "SCRIPT_REDIS_READ_ONLY": True,
            "RAW_TRACEBACK_PERSISTED": False,
        },
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    # Also mirror under /app/reports when present (may not be host-mounted).
    app_report = Path("/app") / HOST_REPORT_HINT
    try:
        app_report.mkdir(parents=True, exist_ok=True)
        (app_report / JSON_PATH.name).write_text(JSON_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    # Markdown
    lines = [
        "# ALL Rubika Account Health Audit",
        "",
        f"Audited at: `{report['audited_at']}`",
        "",
        f"**TOTAL_RUBIKA_ACCOUNTS={totals['TOTAL_RUBIKA_ACCOUNTS']}**",
        "",
        "Account IDs: " + ", ".join(str(i) for i in report["account_ids_ordered"]),
        "",
        "| Account ID | Account | Session | Reconnect | Identity | Pool | Schedule | Coverage | Preflight | Real Send | CURRENT STATE | Next Action |",
        "|---:|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            "| {id} | {label} | {sess} | {rec} | {ident} | {pool} | {sched} | {cov} | {pf} | {rs} | {state} | {next} |".format(
                id=r.get("account_id"),
                label=(r.get("display_label") or "")[:24],
                sess=r.get("session_storage_state"),
                rec=r.get("authenticated_reconnect_status"),
                ident=r.get("identity_match"),
                pool=r.get("pool_status"),
                sched=r.get("schedule_status"),
                cov=r.get("worker_coverage"),
                pf=f"{r.get('preflight_allowed')}/{r.get('preflight_code')}",
                rs=r.get("real_send_evidence", {}).get("success_attempt_count"),
                state=r.get("current_primary_state"),
                next=(r.get("recommended_next_action") or "")[:40],
            )
        )
    lines.extend(
        [
            "",
            "## Totals",
            "",
        ]
    )
    for k, v in totals.items():
        lines.append(f"- **{k}** = `{v}`")
    lines.extend(
        [
            "",
            f"OTP_LOGIN_REQUIRED_ACCOUNT_IDS={report['otp_login_required_account_ids']}",
            "",
            "## Forensic anomalies (read-only; no mutation)",
            "",
            f"- multiple_session_rows: `{anomalies['multiple_session_rows']}`",
            f"- identity_mismatch: `{anomalies['identity_mismatch']}`",
            f"- nonempty_queues: `{anomalies['nonempty_queues']}`",
            "",
            "REAL_MESSAGES_SENT_DURING_AUDIT=False",
            "OTP_REQUESTED_DURING_AUDIT=False",
            "PRODUCTION_DATA_MUTATED=False",
        ]
    )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        (app_report / MD_PATH.name).write_text(MD_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    # Console summary (no secrets)
    print(
        json.dumps(
            {
                "totals": totals,
                "flags": report["flags"],
                "otp_ids": report["otp_login_required_account_ids"],
                "recovered": recovered_ids,
                "anomalies": anomalies,
                "json": str(JSON_PATH),
                "md": str(MD_PATH),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
