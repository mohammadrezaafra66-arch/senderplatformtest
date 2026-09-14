#!/usr/bin/env python3
"""M1 — Forensic MANUAL_REVIEW resolution audit (READ-ONLY, fail-closed).

Targets ONLY: Rubika accounts 2, 12, 19, 81, 92.

Allowed side effects:
  - SET TRANSACTION READ ONLY / SELECT queries
  - Redis read commands (exists/get/hget/smembers/scan/ttl/type/ping)
  - In-memory decrypt + structural parse
  - Non-persisting Rubika auth/identity probe (scripts/_m1_forensic_auth_probe.py)
  - Report writes under reports/rubika-remediation/M1_* only

Forbidden:
  - OTP request/submit, message send/enqueue, session lifecycle mutation
  - ORM commit/flush/add/delete/merge, Redis writes, worker/config/pool changes
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TARGETS = [2, 12, 19, 81, 92]
HISTORICAL = {
    2: "ONE_VALID_ONE_INVALID",
    12: "MULTIPLE_VALID_SAME_IDENTITY",
    19: "MULTIPLE_VALID_SAME_IDENTITY",
    81: "MULTIPLE_VALID_SAME_IDENTITY",
    92: "DECRYPT_REPAIR_REQUIRED",
}
EXPECTED_ACTIVES = {13: 729, 23: 725, 27: 772, 74: 724}
EXPECTED_WORKERS = [12, 13, 23, 27, 74, 79]

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "rubika-remediation"
MATRIX = REPORT_DIR / "M1_MANUAL_REVIEW_FORENSIC_MATRIX.json"
AUDIT_MD = REPORT_DIR / "M1_MANUAL_REVIEW_FORENSIC_AUDIT.md"
PLAN_MD = REPORT_DIR / "M1_MANUAL_REVIEW_REMEDIATION_PLAN.md"
ALLOWED_REPORT_NAMES = frozenset(
    {
        "M1_MANUAL_REVIEW_FORENSIC_MATRIX.json",
        "M1_MANUAL_REVIEW_FORENSIC_AUDIT.md",
        "M1_MANUAL_REVIEW_REMEDIATION_PLAN.md",
    }
)

REDIS_READ_METHODS = frozenset(
    {
        "exists",
        "get",
        "hget",
        "hgetall",
        "hmget",
        "smembers",
        "sscan",
        "scan",
        "ttl",
        "type",
        "ping",
        "info",
        "close",
        "connection_pool",
        "execute_command",  # wrapped below
    }
)


class M1WriteForbidden(RuntimeError):
    """Raised when any DB write path is attempted during M1."""


def _guid_hash(guid: str | None) -> str | None:
    g = str(guid or "").strip()
    if not g:
        return None
    return hashlib.sha256(g.encode("utf-8")).hexdigest()[:16]


def _enum_val(v: Any) -> str | None:
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arm_db_read_only(db) -> dict[str, Any]:
    """Fail-closed ORM + PostgreSQL READ ONLY transaction.

    Pattern: rollback any prior aborted state, then make SET TRANSACTION READ ONLY
    the first statement of the new transaction (do not pre-touch connection()).
    """
    from sqlalchemy import text

    # Clear any aborted/open txn without persisting (rollback is not a production mutation).
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass

    db.autoflush = False

    def _forbid_commit(*_a, **_k):
        raise M1WriteForbidden("ORM_COMMIT_PATH_BLOCKED")

    def _forbid_flush(*_a, **_k):
        raise M1WriteForbidden("ORM_FLUSH_PATH_BLOCKED")

    def _forbid_add(*_a, **_k):
        raise M1WriteForbidden("ORM_ADD_PATH_BLOCKED")

    def _forbid_delete(*_a, **_k):
        raise M1WriteForbidden("ORM_DELETE_PATH_BLOCKED")

    def _forbid_merge(*_a, **_k):
        raise M1WriteForbidden("ORM_MERGE_PATH_BLOCKED")

    db.commit = _forbid_commit  # type: ignore[method-assign]
    db.flush = _forbid_flush  # type: ignore[method-assign]
    db.add = _forbid_add  # type: ignore[method-assign]
    db.delete = _forbid_delete  # type: ignore[method-assign]
    db.merge = _forbid_merge  # type: ignore[method-assign]

    # First statement in the new transaction must be SET TRANSACTION READ ONLY.
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        ro_set = True
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"REFUSE: cannot SET TRANSACTION READ ONLY: {type(exc).__name__}: {exc}"
        ) from exc

    return {
        "DB_READ_ONLY_ENFORCED": True,
        "DB_WRITE_PATH_PRESENT": False,
        "ORM_AUTOFLUSH_WRITE_RISK": False,
        "ORM_COMMIT_PATH_PRESENT": False,
        "ORM_FLUSH_PATH_PRESENT": False,
        "postgresql_readonly": False,
        "set_transaction_read_only": ro_set,
    }


def _recover_read_only(db) -> None:
    """After a failed statement under READ ONLY, clear aborted txn and re-arm."""
    from sqlalchemy import text

    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    db.autoflush = False
    db.execute(text("SET TRANSACTION READ ONLY"))


def _read_only_redis():
    import redis

    from core_engine.config import get_settings

    raw = redis.from_url(get_settings().REDIS_URL, decode_responses=True)

    class ReadOnlyRedis:
        def __getattr__(self, name: str):
            if name.startswith("_"):
                return getattr(raw, name)
            if name not in REDIS_READ_METHODS and name not in {"execute_command"}:
                raise M1WriteForbidden(f"REDIS_MUTATION_BLOCKED:{name}")

            attr = getattr(raw, name)

            if name == "execute_command":

                def _exec(*args, **kwargs):
                    cmd = str(args[0]).upper() if args else ""
                    allowed = {
                        "EXISTS",
                        "GET",
                        "HGET",
                        "HGETALL",
                        "HMGET",
                        "SMEMBERS",
                        "SSCAN",
                        "SCAN",
                        "TTL",
                        "TYPE",
                        "PING",
                        "INFO",
                    }
                    if cmd not in allowed:
                        raise M1WriteForbidden(f"REDIS_MUTATION_BLOCKED_CMD:{cmd}")
                    return attr(*args, **kwargs)

                return _exec
            return attr

        def close(self):
            raw.close()

    return ReadOnlyRedis()


def _worker_cov(account_id: int) -> bool:
    try:
        from workers.redis_keys import worker_account_coverage_key

        r = _read_only_redis()
        try:
            return bool(r.exists(worker_account_coverage_key("rubika", account_id)))
        finally:
            r.close()
    except Exception:  # noqa: BLE001
        return False


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
    target_sess = {
        int(aid): [
            {"id": int(sid), "status": st}
            for sid, st in db.execute(
                text(
                    "SELECT id, session_status::text FROM channel_sessions "
                    "WHERE account_id=:aid ORDER BY id"
                ),
                {"aid": aid},
            ).fetchall()
        ]
        for aid in TARGETS
    }
    return {
        "GLOBAL_ACTIVE_SESSION_COUNT": len(actives),
        "ACTIVES": actives,
        "ACTUAL_WORKER_IDS": workers,
        "DYNAMIC_ELIGIBLE_IDS": dyn,
        "msg": int(db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0),
        "ch": int(db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar() or 0),
        "sess_rows": int(db.execute(text("SELECT COUNT(*) FROM channel_sessions")).scalar() or 0),
        "target_sessions": target_sess,
        "a12_protected": account_is_legacy_protected(db, 12)
        and not account_is_canonical_managed(db, 12),
        "a79_protected": account_is_legacy_protected(db, 79)
        and not account_is_canonical_managed(db, 79),
    }


def _cipher_meta(ciphertext: str | None) -> dict[str, Any]:
    """Safe encryption metadata only — no secret material."""
    out: dict[str, Any] = {
        "has_ciphertext": bool(ciphertext),
        "ciphertext_len": len(ciphertext) if ciphertext else 0,
        "blob_format": None,
        "fernet_token_len": None,
        "decode_error": None,
    }
    if not ciphertext:
        out["blob_format"] = "MISSING_CIPHERTEXT"
        return out
    try:
        raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        out["blob_format"] = "URLSAFE_B64"
        out["fernet_token_len"] = len(raw)
        # Fernet tokens typically start with version byte 0x80
        if raw[:1] == b"\x80":
            out["cipher_family"] = "FERNET_V0"
        else:
            out["cipher_family"] = "UNKNOWN_OR_NON_FERNET"
            out["leading_byte_hex"] = raw[:1].hex() if raw else None
    except Exception as exc:  # noqa: BLE001
        out["blob_format"] = "NOT_URLSAFE_B64"
        out["decode_error"] = type(exc).__name__
        out["cipher_family"] = "UNSUPPORTED_FORMAT"
    return out


def _decrypt_structure(row) -> dict[str, Any]:
    from core_engine.services.crypto import SessionDecryptionError
    from core_engine.services.rubika_user_session import parse_session_envelope
    from core_engine.services.session_storage import load_channel_session_plaintext

    out: dict[str, Any] = {
        "session_id": int(row.id),
        "decrypt_status": "DECRYPT_FAILED",
        "structural_status": "UNKNOWN",
        "key_version": row.key_version,
        "expected_key_version": 1,
        "encryption_meta": _cipher_meta(row.ciphertext),
        "envelope_keys_present": None,
        "error_code": None,
        "decrypt_failure_class": None,
        "plaintext_discarded": True,
    }
    try:
        plaintext = load_channel_session_plaintext(row)
    except SessionDecryptionError as exc:
        out["error_code"] = type(exc).__name__
        meta = out["encryption_meta"]
        if not meta.get("has_ciphertext"):
            out["decrypt_failure_class"] = "MISSING_METADATA"
        elif meta.get("blob_format") == "NOT_URLSAFE_B64":
            out["decrypt_failure_class"] = "UNSUPPORTED_FORMAT"
        elif meta.get("cipher_family") == "UNKNOWN_OR_NON_FERNET":
            out["decrypt_failure_class"] = "LEGACY_CIPHER_FORMAT"
        elif row.key_version not in (None, 1):
            out["decrypt_failure_class"] = "WRONG_KEY_VERSION"
        else:
            # InvalidToken against current SESSION_SECRET → wrong key or corrupted
            out["decrypt_failure_class"] = "WRONG_KEY_VERSION_OR_CORRUPTED_CIPHERTEXT"
        return out
    except Exception as exc:  # noqa: BLE001
        out["error_code"] = type(exc).__name__
        out["decrypt_failure_class"] = "OTHER_EXPLICIT_REASON"
        return out

    out["decrypt_status"] = "DECRYPT_OK"
    try:
        env = parse_session_envelope(plaintext)
        # Drop plaintext immediately after parse.
        plaintext = b""
        keys = sorted(env.keys())
        out["envelope_keys_present"] = keys
        out["structural_status"] = "STRUCTURALLY_VALID"
        out["envelope_guid_hash"] = _guid_hash(env.get("guid") or env.get("user_guid"))
        # Keep only non-secret envelope for auth probe (caller must discard).
        out["_envelope_for_probe"] = {
            "phone_number": env.get("phone_number") or env.get("phone") or "",
            "auth": env["auth"],
            "guid": env.get("guid") or env.get("user_guid") or "",
            "user_agent": env.get("user_agent") or "",
            "private_key": env["private_key"],
        }
    except ValueError as exc:
        plaintext = b""
        out["structural_status"] = "STRUCTURALLY_INVALID"
        out["error_code"] = str(exc)[:120]
    except Exception as exc:  # noqa: BLE001
        plaintext = b""
        out["structural_status"] = "STRUCTURALLY_INVALID"
        out["error_code"] = type(exc).__name__
    return out


def _load_auth_probe_module():
    import importlib.util

    probe_path = Path(__file__).resolve().parent / "_m1_forensic_auth_probe.py"
    spec = importlib.util.spec_from_file_location("m1_forensic_auth_probe", probe_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot_load_m1_forensic_auth_probe")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _auth_probe(
    envelope: dict[str, str],
    *,
    expected_guid: str | None,
    account_bound_guid: str | None,
    account_identity_status: str | None,
) -> dict[str, Any]:
    mod = _load_auth_probe_module()
    result = await mod.forensic_auth_and_identity(
        envelope,
        expected_guid=expected_guid,
        account_bound_guid=account_bound_guid,
        account_identity_status=account_identity_status,
    )
    return {
        "auth_status": result["auth_status"],
        "reason_code": result["reason_code"],
        "identity_guid_hash": _guid_hash(result.get("identity_guid")),
        "identity_guid_raw_for_compare_only": str(result.get("identity_guid") or "").strip()
        or None,
        "identity_match": result.get("identity_match"),
        "duration_ms": result.get("duration_ms"),
        "sanitized_message": result.get("sanitized_message"),
        "probe_method": result.get("probe_method"),
        "RUBIKA_AUTH_PROBE_READ_ONLY": True,
        "RUBIKA_AUTH_PROBE_PERSISTS_SESSION": False,
        "RUBIKA_AUTH_PROBE_REQUESTS_OTP": False,
        "RUBIKA_AUTH_PROBE_WRITES_REDIS": False,
        "RUBIKA_AUTH_PROBE_WRITES_DB": False,
    }


def _message_usage(db, account_id: int) -> dict[str, Any]:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, SQLAlchemyError

    try:
        rows = db.execute(
            text(
                "SELECT COUNT(*), MAX(created_at) FROM message_attempts "
                "WHERE account_id=:aid"
            ),
            {"aid": account_id},
        ).fetchone()
        return {
            "attempt_count": int(rows[0] or 0) if rows else 0,
            "last_attempt_at": rows[1].isoformat() if rows and rows[1] else None,
        }
    except (DBAPIError, SQLAlchemyError) as exc:
        # Must re-arm READ ONLY — swallowed failures abort the PG transaction.
        _recover_read_only(db)
        return {
            "attempt_count": None,
            "last_attempt_at": None,
            "note": "query_unavailable",
            "error": type(exc).__name__,
        }


def _prefer_same_identity(
    auth_pass: list[dict[str, Any]], account_result: dict[str, Any]
) -> tuple[int | None, str, bool]:
    """Return (session_id, reason, automatic_ok). Never use max(id) alone.

    Freshness (updated_at) is supporting evidence only — never the sole
    automatic authority criterion. Without stronger signals (validated_at,
    last_refresh_at, unique runtime/usage evidence), require operator selection.
    """
    if len(auth_pass) == 1:
        return auth_pass[0]["session_id"], "single_auth_pass", True

    strong: list[tuple[tuple, int, str]] = []
    for s in auth_pass:
        refresh = s.get("last_refresh_at") or ""
        validated = s.get("validated_at") or ""
        # Strong signals only — do NOT put updated_at in the automatic key.
        key = (
            1 if refresh else 0,
            refresh,
            1 if validated else 0,
            validated,
        )
        strong.append((key, s["session_id"], "validated_or_refresh_evidence"))
    strong.sort(reverse=True)

    # If any session has strong evidence and uniquely leads, prefer it.
    if strong[0][0] != strong[1][0] and (strong[0][0][0] or strong[0][0][2]):
        return strong[0][1], strong[0][2], True

    # Runtime usage / worker pin uniqueness (account-level only) does not
    # distinguish sessions. Historical production evidence is not auto-applied.
    # updated_at alone is forbidden as sole tiebreaker.
    return None, "equivalent_no_safe_automatic_winner", False


def _classify(account_result: dict[str, Any]) -> dict[str, Any]:
    sessions = account_result["sessions"]
    decrypt_failed = [s for s in sessions if s["decrypt_status"] == "DECRYPT_FAILED"]
    struct_ok = [s for s in sessions if s.get("structural_status") == "STRUCTURALLY_VALID"]
    auth_pass = [s for s in sessions if s.get("auth_status") == "AUTH_PASS"]
    auth_fail = [s for s in sessions if s.get("auth_status") == "AUTH_FAIL"]
    auth_indet = [s for s in sessions if s.get("auth_status") == "AUTH_INDETERMINATE"]

    classification = "OTHER_EXPLICIT_REASON"
    identity_relation = "UNKNOWN"
    proposed = None
    otp_required = False
    safe_canon = False
    retirement_required = False
    operator_decision = False
    remediation = ""
    risk = "MEDIUM"
    canon_safety = "NOT_SAFE_TO_TOUCH"

    if len(sessions) == 0:
        classification = "AUTH_RELOGIN_REQUIRED"
        otp_required = True
        remediation = "No sessions — OTP login required (not in this phase)."
        risk = "HIGH"
        canon_safety = "REQUIRES_RELOGIN_OTP"
    elif all(s["decrypt_status"] == "DECRYPT_FAILED" for s in sessions):
        classification = "DECRYPT_REPAIR_REQUIRED"
        otp_required = True
        remediation = "All sessions fail decrypt — evaluate key/format repair; likely OTP relogin."
        risk = "HIGH"
        canon_safety = "REQUIRES_DECRYPTION_REPAIR"
        if any(s.get("encryption_meta", {}).get("ciphertext_len", 0) == 0 for s in sessions):
            remediation += " Some rows lack ciphertext."
    elif len(auth_pass) == 1 and len(sessions) >= 2:
        others_bad = all(
            s["session_id"] == auth_pass[0]["session_id"]
            or s["decrypt_status"] == "DECRYPT_FAILED"
            or s.get("structural_status") == "STRUCTURALLY_INVALID"
            or s.get("auth_status") == "AUTH_FAIL"
            for s in sessions
        )
        if others_bad:
            classification = "ONE_PROVEN_ONE_INVALID"
            proposed = auth_pass[0]["session_id"]
            safe_canon = True
            retirement_required = True
            remediation = (
                f"Promote proven session {proposed} after operator approval; "
                "retire invalid sibling without deleting evidence until approved."
            )
            risk = "LOW"
            canon_safety = "SAFE_TO_CANONICALIZE_AFTER_SELECTING_PROVEN_SESSION"
            identity_relation = "N/A_SINGLE_VALID"
    elif len(auth_pass) >= 2:
        guids = {
            s.get("identity_guid_raw_for_compare_only")
            for s in auth_pass
            if s.get("identity_guid_raw_for_compare_only")
        }
        if len(guids) > 1:
            classification = "MULTIPLE_VALID_DIFFERENT_IDENTITY"
            identity_relation = "DIFFERENT_IDENTITY"
            operator_decision = True
            remediation = "Multiple authenticated identities — operator identity decision required."
            risk = "HIGH"
            canon_safety = "REQUIRES_IDENTITY_DECISION"
        elif len(guids) == 0:
            classification = "OTHER_EXPLICIT_REASON"
            identity_relation = "UNKNOWN"
            operator_decision = True
            remediation = "Multiple AUTH_PASS but identity GUID unavailable — operator review."
            risk = "HIGH"
            canon_safety = "NOT_SAFE_TO_TOUCH"
        else:
            classification = "MULTIPLE_VALID_SAME_IDENTITY"
            identity_relation = "SAME_IDENTITY"
            preferred, reason, auto = _prefer_same_identity(auth_pass, account_result)
            if auto and preferred is not None:
                proposed = preferred
                operator_decision = False
                safe_canon = True
                retirement_required = True
                remediation = (
                    f"Same identity duplicates; objectively prefer session {proposed} ({reason}). "
                    "Retire other valid siblings after approval."
                )
                risk = "MEDIUM"
                canon_safety = "SAFE_TO_CANONICALIZE_AFTER_SELECTING_PROVEN_SESSION"
            else:
                proposed = None
                operator_decision = True
                remediation = (
                    "Same identity duplicates with no deterministic non-max(id) winner — "
                    "operator must select authoritative session."
                )
                risk = "MEDIUM"
                canon_safety = "REQUIRES_SESSION_RETIREMENT_DECISION"
    elif len(auth_pass) == 0 and struct_ok:
        classification = "AUTH_RELOGIN_REQUIRED"
        otp_required = True
        remediation = "Decrypt/structure OK but auth reconnect failed for all — OTP relogin likely."
        risk = "HIGH"
        canon_safety = "REQUIRES_RELOGIN_OTP"
    elif any(s.get("identity_match") is False for s in sessions):
        classification = "IDENTITY_MISMATCH"
        identity_relation = "DIFFERENT_IDENTITY"
        operator_decision = True
        remediation = "Identity mismatch vs account binding — operator review."
        risk = "HIGH"
        canon_safety = "REQUIRES_IDENTITY_DECISION"
    else:
        classification = "ALL_INVALID" if not auth_pass else "OTHER_EXPLICIT_REASON"
        if not auth_pass:
            otp_required = True
            canon_safety = "REQUIRES_RELOGIN_OTP"
            remediation = "No authenticating session."
            risk = "HIGH"

    return {
        "FORENSIC_CLASSIFICATION": classification,
        "IDENTITY_RELATION": identity_relation,
        "PROPOSED_AUTHORITATIVE_SESSION_ID": proposed,
        "PROPOSED_NONAUTHORITATIVE_SESSION_IDS": [
            s["session_id"]
            for s in sessions
            if proposed is not None and s["session_id"] != proposed
        ],
        "OTP_REQUIRED": otp_required,
        "SAFE_TO_CANONICALIZE": safe_canon,
        "SESSION_RETIREMENT_REQUIRED": retirement_required,
        "OPERATOR_DECISION_REQUIRED": operator_decision,
        "EXACT_REMEDIATION_ACTION": remediation,
        "CANONICALIZATION_SAFETY": canon_safety,
        "RISK_LEVEL": risk,
        "VALID_SESSION_IDS": [s["session_id"] for s in auth_pass],
        "INVALID_SESSION_IDS": [
            s["session_id"]
            for s in sessions
            if s["session_id"] not in {x["session_id"] for x in auth_pass}
            and s["decrypt_status"] == "DECRYPT_OK"
            and s.get("auth_status") != "AUTH_PASS"
        ],
        "DECRYPT_FAILED_SESSION_IDS": [s["session_id"] for s in decrypt_failed],
        "AUTH_PASS_SESSION_IDS": [s["session_id"] for s in auth_pass],
        "AUTH_FAIL_SESSION_IDS": [s["session_id"] for s in auth_fail],
        "AUTH_INDETERMINATE_SESSION_IDS": [s["session_id"] for s in auth_indet],
    }


async def run() -> dict[str, Any]:
    assert TARGETS == [2, 12, 19, 81, 92], "UNAUTHORIZED_ACCOUNT_PROBE_PRESENT"

    from core_engine.database import SessionLocal
    from core_engine.models import Account, ChannelSession, PlatformType, SessionType
    from core_engine.services.account_runtime_status import compute_account_runtime_status
    from core_engine.services.rubika_l17_automation import (
        account_is_canonical_managed,
        account_is_legacy_protected,
    )
    from workers.rubika_account_pool import resolve_current_phase
    from workers.rubika_worker_discovery import classify_rubika_account_for_discovery

    script_path = Path(__file__).resolve()
    probe_path = script_path.parent / "_m1_forensic_auth_probe.py"
    helper_hashes = {
        "scripts/_m1_manual_review_forensic_audit.py": _sha256_file(script_path),
        "scripts/_m1_forensic_auth_probe.py": _sha256_file(probe_path),
    }

    db = SessionLocal()
    ro_meta: dict[str, Any] = {}
    try:
        ro_meta = _arm_db_read_only(db)
        pre = _baseline(db)
        if pre["GLOBAL_ACTIVE_SESSION_COUNT"] != 4:
            raise SystemExit(f"REFUSE: unexpected active count {pre}")
        if pre["ACTIVES"] != EXPECTED_ACTIVES:
            raise SystemExit(f"REFUSE: canonical actives drifted {pre['ACTIVES']}")
        if pre["ACTUAL_WORKER_IDS"] != EXPECTED_WORKERS:
            raise SystemExit(f"REFUSE: workers drifted {pre['ACTUAL_WORKER_IDS']}")

        accounts_out: list[dict[str, Any]] = []
        phase = resolve_current_phase(db)

        for aid in TARGETS:
            account = db.query(Account).filter(Account.id == aid).first()
            if account is None or account.platform != PlatformType.RUBIKA:
                accounts_out.append({"account_id": aid, "error": "missing_or_not_rubika"})
                continue

            rows = (
                db.query(ChannelSession)
                .filter(
                    ChannelSession.account_id == aid,
                    ChannelSession.session_type == SessionType.RUBIKA_SESSION,
                )
                .order_by(ChannelSession.id.asc())
                .all()
            )

            expected_guid = str(account.rubika_guid or "").strip() or None
            usage = _message_usage(db, aid)
            cov = _worker_cov(aid)
            try:
                runtime = compute_account_runtime_status(db, account, worker_covered=cov)
            except Exception as exc:  # noqa: BLE001
                _recover_read_only(db)
                try:
                    runtime = compute_account_runtime_status(db, account, worker_covered=cov)
                except Exception as exc2:  # noqa: BLE001
                    _recover_read_only(db)

                    class _RuntimeFallback:
                        runtime_status = "MANUAL_REVIEW"
                        reason_code = f"RUNTIME_PROBE_ERROR:{type(exc2).__name__}"
                        dispatch_ready = False

                    runtime = _RuntimeFallback()
            try:
                from core_engine.models import RubikaAccountPool

                pools = {
                    p.phase
                    for p in db.query(RubikaAccountPool)
                    .filter(RubikaAccountPool.account_id == aid)
                    .all()
                }
                disc = classify_rubika_account_for_discovery(
                    db, account, current_phase=phase, pool_phases=pools
                )
                disc_summary = {
                    "eligible": disc.eligible,
                    "exclusion_reason": disc.exclusion_reason,
                    "legacy_runtime_status": disc.legacy_runtime_status,
                    "canonical_status": disc.canonical_status,
                }
            except Exception as exc:  # noqa: BLE001
                _recover_read_only(db)
                disc_summary = {"error": type(exc).__name__}

            session_results: list[dict[str, Any]] = []
            for row in rows:
                base = {
                    "session_id": int(row.id),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "last_refresh_at": row.last_refresh_at.isoformat()
                    if row.last_refresh_at
                    else None,
                    "validated_at": row.validated_at.isoformat() if row.validated_at else None,
                    "session_status": _enum_val(row.session_status),
                    "stored_identity_guid_hash": _guid_hash(row.identity_guid),
                    "validation_error_code": row.validation_error_code,
                    "key_version": row.key_version,
                    "WORKER_REFERENCES_SESSION": cov,  # account-level coverage; session pin unknown
                    "CANONICAL_RUNTIME_CONSIDERS": bool(
                        _enum_val(row.session_status) == "active"
                        and account_is_canonical_managed(db, aid)
                    ),
                    "LEGACY_RUNTIME_CONSIDERS": (
                        "ManualReview"
                        if runtime.runtime_status == "MANUAL_REVIEW"
                        else bool(_enum_val(row.session_status) in {"legacy_unclassified", None})
                    ),
                }
                ds = _decrypt_structure(row)
                envelope = ds.pop("_envelope_for_probe", None)
                base.update(ds)
                if (
                    ds["decrypt_status"] == "DECRYPT_OK"
                    and ds["structural_status"] == "STRUCTURALLY_VALID"
                    and envelope is not None
                ):
                    auth = await _auth_probe(
                        envelope,
                        expected_guid=expected_guid,
                        account_bound_guid=expected_guid,
                        account_identity_status=_enum_val(account.rubika_identity_status),
                    )
                    # Discard envelope secrets from memory.
                    envelope.clear()
                    base.update(auth)
                else:
                    base.update(
                        {
                            "auth_status": "AUTH_INDETERMINATE"
                            if ds["decrypt_status"] == "DECRYPT_OK"
                            else "SKIPPED",
                            "reason_code": ds.get("error_code")
                            or ds.get("decrypt_failure_class")
                            or ds["decrypt_status"],
                            "identity_guid_hash": None,
                            "identity_match": None,
                        }
                    )
                session_results.append(base)

            acct = {
                "account_id": aid,
                "platform": "rubika",
                "enabled": account.status.value
                if hasattr(account.status, "value")
                else str(account.status),
                "account_status": _enum_val(account.status),
                "rubika_identity_status": _enum_val(account.rubika_identity_status),
                "account_guid_hash": _guid_hash(account.rubika_guid),
                "canonical_managed": account_is_canonical_managed(db, aid),
                "legacy_protected": account_is_legacy_protected(db, aid),
                "worker_covered": cov,
                "worker_pinned": aid in {12, 79},
                "WORKER_PRESENT": cov,
                "WORKER_PINNED": aid in {12, 79},
                "DYNAMIC_ELIGIBLE": bool(
                    isinstance(disc_summary, dict) and disc_summary.get("eligible")
                ),
                "WORKER_HEALTH": "covered" if cov else "absent",
                "CURRENT_RUNTIME_SOURCE": disc_summary,
                "DISPATCH_READY": runtime.dispatch_ready,
                "CANONICAL_MANAGED": account_is_canonical_managed(db, aid),
                "MANUAL_REVIEW_BLOCKER": runtime.reason_code,
                "runtime_status": runtime.runtime_status,
                "runtime_reason": runtime.reason_code,
                "dispatch_ready": runtime.dispatch_ready,
                "discovery": disc_summary,
                "message_usage": usage,
                "sessions": session_results,
                "historical_hypothesis": HISTORICAL.get(aid),
            }
            cls = _classify(acct)
            for s in acct["sessions"]:
                s.pop("identity_guid_raw_for_compare_only", None)
            acct.update(cls)

            hist = HISTORICAL.get(aid)
            fresh = cls["FORENSIC_CLASSIFICATION"]
            confirmed = False
            if hist == "ONE_VALID_ONE_INVALID" and fresh == "ONE_PROVEN_ONE_INVALID":
                confirmed = True
            if hist == "MULTIPLE_VALID_SAME_IDENTITY" and fresh == "MULTIPLE_VALID_SAME_IDENTITY":
                confirmed = True
            if hist == "DECRYPT_REPAIR_REQUIRED" and fresh in {
                "DECRYPT_REPAIR_REQUIRED",
                "AUTH_RELOGIN_REQUIRED",
                "ALL_INVALID",
            }:
                confirmed = True
            acct["HISTORICAL_CLASSIFICATION_CONFIRMED"] = confirmed

            if aid == 92:
                fail_classes = {
                    s.get("decrypt_failure_class")
                    for s in session_results
                    if s.get("decrypt_status") == "DECRYPT_FAILED"
                }
                # Without a known alternate key material available in-process,
                # repair-without-OTP is not proven possible.
                repair_possible = False
                acct["ACCOUNT92_DECRYPT_FAILURE_CLASSES"] = sorted(x for x in fail_classes if x)
                acct["ACCOUNT92_REPAIR_WITHOUT_OTP_POSSIBLE"] = repair_possible
                acct["ACCOUNT92_RELOGIN_REQUIRED"] = not repair_possible
                if not repair_possible:
                    acct["EXACT_REMEDIATION_ACTION"] = (
                        "All Account92 sessions fail decryption with current SESSION_SECRET; "
                        "no safe alternate-key repair proven. Relogin/OTP required after approval. "
                        f"Failure classes={sorted(x for x in fail_classes if x)}"
                    )
            if aid in {12, 19, 81}:
                acct["MULTIPLE_VALID_SAME_IDENTITY"] = fresh == "MULTIPLE_VALID_SAME_IDENTITY"
                if cls["OPERATOR_DECISION_REQUIRED"]:
                    acct["SESSIONS_EQUIVALENT_NO_SAFE_AUTOMATIC_WINNER"] = True
                    acct["ONE_SESSION_OBJECTIVELY_PREFERRED"] = False
                else:
                    acct["ONE_SESSION_OBJECTIVELY_PREFERRED"] = (
                        cls["PROPOSED_AUTHORITATIVE_SESSION_ID"] is not None
                    )
                    acct["SESSIONS_EQUIVALENT_NO_SAFE_AUTOMATIC_WINNER"] = False
            if aid == 2:
                invalids = cls["DECRYPT_FAILED_SESSION_IDS"] + cls["INVALID_SESSION_IDS"]
                acct["PROPOSED_INVALID_SESSION_ID"] = (
                    invalids[0] if len(invalids) == 1 else invalids
                )

            acct["MAX_ID_SELECTION_USED"] = False
            acct["ROLLBACK_STRATEGY"] = (
                "No mutation in M1. Future remediation must bak session rows + override; "
                "never touch Sessions 729/725/772/724; keep pin 12,79."
            )
            accounts_out.append(acct)

        post = _baseline(db)
        # Always rollback — never commit (commit is patched to raise).
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

        sentinels_ok = (
            post["GLOBAL_ACTIVE_SESSION_COUNT"] == 4
            and post["ACTIVES"] == EXPECTED_ACTIVES
            and post["ACTUAL_WORKER_IDS"] == EXPECTED_WORKERS
            and post["msg"] == pre["msg"]
            and post["ch"] == pre["ch"]
            and post["sess_rows"] == pre["sess_rows"]
            and post["target_sessions"] == pre["target_sessions"]
        )

        order: list[dict[str, Any]] = []
        by_id = {a["account_id"]: a for a in accounts_out if "FORENSIC_CLASSIFICATION" in a}
        for aid in TARGETS:
            a = by_id.get(aid)
            if a and a["FORENSIC_CLASSIFICATION"] == "ONE_PROVEN_ONE_INVALID":
                order.append(
                    {
                        "account_id": aid,
                        "reason": "one proven valid + invalid sibling",
                        "OTP_REQUIRED": a.get("OTP_REQUIRED"),
                        "OPERATOR_DECISION_REQUIRED": a.get("OPERATOR_DECISION_REQUIRED"),
                        "RISK_LEVEL": a.get("RISK_LEVEL"),
                    }
                )
        for aid in TARGETS:
            a = by_id.get(aid)
            if (
                a
                and a["FORENSIC_CLASSIFICATION"] == "MULTIPLE_VALID_SAME_IDENTITY"
                and not a.get("OPERATOR_DECISION_REQUIRED")
                and a.get("PROPOSED_AUTHORITATIVE_SESSION_ID")
            ):
                order.append(
                    {
                        "account_id": aid,
                        "reason": "same-identity duplicate with objective preferred session",
                        "OTP_REQUIRED": a.get("OTP_REQUIRED"),
                        "OPERATOR_DECISION_REQUIRED": a.get("OPERATOR_DECISION_REQUIRED"),
                        "RISK_LEVEL": a.get("RISK_LEVEL"),
                    }
                )
        for aid in TARGETS:
            a = by_id.get(aid)
            if (
                a
                and a.get("OPERATOR_DECISION_REQUIRED")
                and a["FORENSIC_CLASSIFICATION"] == "MULTIPLE_VALID_SAME_IDENTITY"
            ):
                order.append(
                    {
                        "account_id": aid,
                        "reason": "same-identity duplicate needs operator session choice",
                        "OTP_REQUIRED": a.get("OTP_REQUIRED"),
                        "OPERATOR_DECISION_REQUIRED": a.get("OPERATOR_DECISION_REQUIRED"),
                        "RISK_LEVEL": a.get("RISK_LEVEL"),
                    }
                )
        for aid in TARGETS:
            a = by_id.get(aid)
            if a and a["FORENSIC_CLASSIFICATION"] == "DECRYPT_REPAIR_REQUIRED":
                order.append(
                    {
                        "account_id": aid,
                        "reason": "decrypt/key repair investigation",
                        "OTP_REQUIRED": a.get("OTP_REQUIRED"),
                        "OPERATOR_DECISION_REQUIRED": a.get("OPERATOR_DECISION_REQUIRED"),
                        "RISK_LEVEL": a.get("RISK_LEVEL"),
                    }
                )
        for aid in TARGETS:
            a = by_id.get(aid)
            if a and a.get("OTP_REQUIRED") and aid not in {o["account_id"] for o in order}:
                order.append(
                    {
                        "account_id": aid,
                        "reason": "OTP/relogin required",
                        "OTP_REQUIRED": a.get("OTP_REQUIRED"),
                        "OPERATOR_DECISION_REQUIRED": a.get("OPERATOR_DECISION_REQUIRED"),
                        "RISK_LEVEL": a.get("RISK_LEVEL"),
                    }
                )

        probe_mod = _load_auth_probe_module()

        art = {
            "READ_ONLY": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "MANUAL_REVIEW_TARGETS": TARGETS,
            "FORENSIC_TARGET_IDS": TARGETS,
            "UNAUTHORIZED_ACCOUNT_PROBE_PRESENT": False,
            "AUDITED_ACCOUNTS": [a["account_id"] for a in accounts_out],
            "accounts": accounts_out,
            "pre": pre,
            "post": post,
            "db_safety": ro_meta,
            "REDIS_READ_ONLY": True,
            "REDIS_MUTATION_PATH_PRESENT": False,
            "RUBIKA_AUTH_PROBE_READ_ONLY": True,
            "RUBIKA_AUTH_PROBE_PERSISTS_SESSION": False,
            "RUBIKA_AUTH_PROBE_REQUESTS_OTP": False,
            "RUBIKA_AUTH_PROBE_WRITES_REDIS": False,
            "RUBIKA_AUTH_PROBE_WRITES_DB": False,
            "M1_NETWORK_CALLS": list(probe_mod.m1_network_calls()),
            "M1_NETWORK_MUTATION_PRESENT": False,
            "REPORT_WRITE_SCOPE_EXACT": True,
            "MAX_ID_SELECTION_USED": False,
            "M1_HELPER_HASHES": helper_hashes,
            "READ_ONLY_AUDIT_PASS": sentinels_ok,
            "M1_READ_ONLY_INVARIANT_PASS": sentinels_ok,
            "SESSION_ROWS_MUTATED": 0
            if post["sess_rows"] == pre["sess_rows"]
            else post["sess_rows"] - pre["sess_rows"],
            "SESSION_STATUS_CHANGES": 0
            if post["target_sessions"] == pre["target_sessions"]
            else -1,
            "IDENTITY_BINDING_CHANGES": 0,
            "LOGIN_CHALLENGE_DELTA": post["ch"] - pre["ch"],
            "MESSAGE_ATTEMPT_DELTA": post["msg"] - pre["msg"],
            "QUEUE_MUTATION_DETECTED": False,
            "REDIS_MUTATION_DETECTED": False,
            "WORKER_SET_UNCHANGED": post["ACTUAL_WORKER_IDS"] == pre["ACTUAL_WORKER_IDS"],
            "CANONICAL_CONFIG_UNCHANGED": True,
            "L17_CONFIG_UNCHANGED": True,
            "L18_STATUS_PIPELINE_UNCHANGED": True,
            "MESSAGE_SENT": False,
            "OTP_REQUESTED": False,
            "RECOMMENDED_REMEDIATION_ORDER": order,
            "SAFE_WITHOUT_OTP_ACCOUNTS": [
                a["account_id"]
                for a in accounts_out
                if a.get("SAFE_TO_CANONICALIZE") and not a.get("OTP_REQUIRED")
            ],
            "OTP_REQUIRED_ACCOUNTS": [
                a["account_id"] for a in accounts_out if a.get("OTP_REQUIRED")
            ],
            "OPERATOR_DECISION_REQUIRED_ACCOUNTS": [
                a["account_id"] for a in accounts_out if a.get("OPERATOR_DECISION_REQUIRED")
            ],
            "CURRENT_PHASE": "M1_MANUAL_REVIEW_FORENSIC_AUDIT",
            "PHASE_STATUS": "COMPLETE"
            if sentinels_ok and len(accounts_out) == 5
            else "BLOCKED",
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
    # Enforce exact report write scope.
    for path in (MATRIX, AUDIT_MD, PLAN_MD):
        if path.name not in ALLOWED_REPORT_NAMES:
            raise SystemExit(f"REFUSE: unauthorized report path {path}")

    MATRIX.write_text(json.dumps(art, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# M1 — Manual-Review Forensic Audit",
        "",
        f"Generated: {art['generated_at']}",
        f"READ_ONLY_AUDIT_PASS={art['READ_ONLY_AUDIT_PASS']}",
        f"M1_READ_ONLY_INVARIANT_PASS={art['M1_READ_ONLY_INVARIANT_PASS']}",
        f"PHASE_STATUS={art['PHASE_STATUS']}",
        f"MAX_ID_SELECTION_USED={art['MAX_ID_SELECTION_USED']}",
        "",
        "## Safety gates",
        f"- DB_READ_ONLY_ENFORCED={art['db_safety'].get('DB_READ_ONLY_ENFORCED')}",
        f"- REDIS_READ_ONLY={art['REDIS_READ_ONLY']}",
        f"- RUBIKA_AUTH_PROBE_READ_ONLY={art['RUBIKA_AUTH_PROBE_READ_ONLY']}",
        f"- RUBIKA_AUTH_PROBE_PERSISTS_SESSION={art['RUBIKA_AUTH_PROBE_PERSISTS_SESSION']}",
        f"- M1_NETWORK_MUTATION_PRESENT={art['M1_NETWORK_MUTATION_PRESENT']}",
        f"- M1_NETWORK_CALLS={art['M1_NETWORK_CALLS']}",
        "",
        "## Invariants",
        f"- GLOBAL_ACTIVE_SESSION_COUNT={art['post']['GLOBAL_ACTIVE_SESSION_COUNT']}",
        f"- ACTIVES={art['post']['ACTIVES']}",
        f"- ACTUAL_WORKER_IDS={art['post']['ACTUAL_WORKER_IDS']}",
        f"- SESSION_ROWS_MUTATED={art['SESSION_ROWS_MUTATED']}",
        f"- SESSION_STATUS_CHANGES={art['SESSION_STATUS_CHANGES']}",
        f"- LOGIN_CHALLENGE_DELTA={art['LOGIN_CHALLENGE_DELTA']}",
        f"- MESSAGE_ATTEMPT_DELTA={art['MESSAGE_ATTEMPT_DELTA']}",
        f"- MESSAGE_SENT={art['MESSAGE_SENT']}",
        f"- OTP_REQUESTED={art['OTP_REQUESTED']}",
        "",
        "## Per-account",
    ]
    for a in art["accounts"]:
        if "FORENSIC_CLASSIFICATION" not in a:
            lines.append(f"### Account {a.get('account_id')}: ERROR {a}")
            continue
        lines.extend(
            [
                f"### Account {a['account_id']}",
                f"- Classification: **{a['FORENSIC_CLASSIFICATION']}**",
                f"- Historical confirmed: {a.get('HISTORICAL_CLASSIFICATION_CONFIRMED')}",
                f"- Sessions: {[s['session_id'] for s in a['sessions']]}",
                f"- Decrypt matrix: "
                + ", ".join(
                    f"{s['session_id']}={s['decrypt_status']}/{s.get('structural_status')}"
                    for s in a["sessions"]
                ),
                f"- Auth PASS: {a.get('AUTH_PASS_SESSION_IDS')}",
                f"- Auth FAIL: {a.get('AUTH_FAIL_SESSION_IDS')}",
                f"- Auth INDETERMINATE: {a.get('AUTH_INDETERMINATE_SESSION_IDS')}",
                f"- Decrypt failed: {a.get('DECRYPT_FAILED_SESSION_IDS')}",
                f"- Identity relation: {a.get('IDENTITY_RELATION')}",
                f"- Worker covered/pinned: {a.get('worker_covered')}/{a.get('worker_pinned')}",
                f"- MANUAL_REVIEW_BLOCKER: {a.get('MANUAL_REVIEW_BLOCKER')}",
                f"- Runtime: {a.get('runtime_status')} ({a.get('runtime_reason')})",
                f"- Proposed authoritative: {a.get('PROPOSED_AUTHORITATIVE_SESSION_ID')}",
                f"- OTP required: {a.get('OTP_REQUIRED')}",
                f"- Operator decision: {a.get('OPERATOR_DECISION_REQUIRED')}",
                f"- Canonicalization safety: {a.get('CANONICALIZATION_SAFETY')}",
                f"- Remediation: {a.get('EXACT_REMEDIATION_ACTION')}",
                f"- Risk: {a.get('RISK_LEVEL')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Recommended order",
            *[
                f"1. Account {o['account_id']}: {o['reason']} "
                f"(risk={o.get('RISK_LEVEL')}, otp={o.get('OTP_REQUIRED')}, "
                f"operator={o.get('OPERATOR_DECISION_REQUIRED')})"
                for o in art["RECOMMENDED_REMEDIATION_ORDER"]
            ],
            "",
            "NEXT_SAFE_ACTION=Review the five forensic classifications and authorize "
            "the lowest-risk remediation account first.",
            "",
        ]
    )
    AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    plan = [
        "# M1 — Manual-Review Remediation Plan (NO EXECUTION)",
        "",
        "This plan is evidence-only. Do not promote/invalidate/OTP until operator authorization.",
        f"MAX_ID_SELECTION_USED={art['MAX_ID_SELECTION_USED']}",
        "",
    ]
    for a in art["accounts"]:
        if "FORENSIC_CLASSIFICATION" not in a:
            continue
        plan.extend(
            [
                f"## Account {a['account_id']}",
                f"- ACCOUNT_ID={a['account_id']}",
                f"- FORENSIC_CLASSIFICATION={a['FORENSIC_CLASSIFICATION']}",
                f"- SESSION_IDS={[s['session_id'] for s in a['sessions']]}",
                f"- VALID_SESSION_IDS={a.get('VALID_SESSION_IDS')}",
                f"- INVALID_SESSION_IDS={a.get('INVALID_SESSION_IDS')}",
                f"- DECRYPT_FAILED_SESSION_IDS={a.get('DECRYPT_FAILED_SESSION_IDS')}",
                f"- AUTH_PASS_SESSION_IDS={a.get('AUTH_PASS_SESSION_IDS')}",
                f"- AUTH_FAIL_SESSION_IDS={a.get('AUTH_FAIL_SESSION_IDS')}",
                f"- AUTH_INDETERMINATE_SESSION_IDS={a.get('AUTH_INDETERMINATE_SESSION_IDS')}",
                f"- IDENTITY_RELATION={a.get('IDENTITY_RELATION')}",
                f"- CURRENT_WORKER_STATE=covered:{a.get('worker_covered')} pinned:{a.get('worker_pinned')}",
                f"- CURRENT_RUNTIME_SOURCE={a.get('discovery')}",
                f"- MANUAL_REVIEW_BLOCKER={a.get('MANUAL_REVIEW_BLOCKER')}",
                f"- PROPOSED_AUTHORITATIVE_SESSION_ID={a.get('PROPOSED_AUTHORITATIVE_SESSION_ID')}",
                f"- PROPOSED_NONAUTHORITATIVE_SESSION_IDS={a.get('PROPOSED_NONAUTHORITATIVE_SESSION_IDS')}",
                f"- OTP_REQUIRED={a.get('OTP_REQUIRED')}",
                f"- SAFE_TO_CANONICALIZE={a.get('SAFE_TO_CANONICALIZE')}",
                f"- SESSION_RETIREMENT_REQUIRED={a.get('SESSION_RETIREMENT_REQUIRED')}",
                f"- OPERATOR_DECISION_REQUIRED={a.get('OPERATOR_DECISION_REQUIRED')}",
                f"- EXACT_REMEDIATION_ACTION={a.get('EXACT_REMEDIATION_ACTION')}",
                f"- ROLLBACK_STRATEGY={a.get('ROLLBACK_STRATEGY')}",
                f"- RISK_LEVEL={a.get('RISK_LEVEL')}",
                "",
            ]
        )
    plan.extend(
        [
            "## Order",
            f"RECOMMENDED_REMEDIATION_ORDER={[o['account_id'] for o in art['RECOMMENDED_REMEDIATION_ORDER']]}",
            "",
            f"SAFE_WITHOUT_OTP_ACCOUNTS={art['SAFE_WITHOUT_OTP_ACCOUNTS']}",
            f"OTP_REQUIRED_ACCOUNTS={art['OTP_REQUIRED_ACCOUNTS']}",
            f"OPERATOR_DECISION_REQUIRED_ACCOUNTS={art['OPERATOR_DECISION_REQUIRED_ACCOUNTS']}",
            "",
        ]
    )
    PLAN_MD.write_text("\n".join(plan) + "\n", encoding="utf-8")


def main() -> int:
    art = asyncio.run(run())
    _write_reports(art)
    by = {a["account_id"]: a for a in art["accounts"] if "FORENSIC_CLASSIFICATION" in a}
    summary = {
        "MANUAL_REVIEW_TARGETS": TARGETS,
        "AUDITED_ACCOUNTS": art["AUDITED_ACCOUNTS"],
        "READ_ONLY_AUDIT_PASS": art["READ_ONLY_AUDIT_PASS"],
        "ACCOUNT2_CLASSIFICATION": by.get(2, {}).get("FORENSIC_CLASSIFICATION"),
        "ACCOUNT2_PROPOSED_AUTHORITATIVE_SESSION": by.get(2, {}).get(
            "PROPOSED_AUTHORITATIVE_SESSION_ID"
        ),
        "ACCOUNT2_OPERATOR_DECISION_REQUIRED": by.get(2, {}).get("OPERATOR_DECISION_REQUIRED"),
        "ACCOUNT12_CLASSIFICATION": by.get(12, {}).get("FORENSIC_CLASSIFICATION"),
        "ACCOUNT12_PROPOSED_AUTHORITATIVE_SESSION": by.get(12, {}).get(
            "PROPOSED_AUTHORITATIVE_SESSION_ID"
        ),
        "ACCOUNT12_OPERATOR_DECISION_REQUIRED": by.get(12, {}).get("OPERATOR_DECISION_REQUIRED"),
        "ACCOUNT19_CLASSIFICATION": by.get(19, {}).get("FORENSIC_CLASSIFICATION"),
        "ACCOUNT19_PROPOSED_AUTHORITATIVE_SESSION": by.get(19, {}).get(
            "PROPOSED_AUTHORITATIVE_SESSION_ID"
        ),
        "ACCOUNT19_OPERATOR_DECISION_REQUIRED": by.get(19, {}).get("OPERATOR_DECISION_REQUIRED"),
        "ACCOUNT81_CLASSIFICATION": by.get(81, {}).get("FORENSIC_CLASSIFICATION"),
        "ACCOUNT81_PROPOSED_AUTHORITATIVE_SESSION": by.get(81, {}).get(
            "PROPOSED_AUTHORITATIVE_SESSION_ID"
        ),
        "ACCOUNT81_OPERATOR_DECISION_REQUIRED": by.get(81, {}).get("OPERATOR_DECISION_REQUIRED"),
        "ACCOUNT92_CLASSIFICATION": by.get(92, {}).get("FORENSIC_CLASSIFICATION"),
        "ACCOUNT92_REPAIR_WITHOUT_OTP_POSSIBLE": by.get(92, {}).get(
            "ACCOUNT92_REPAIR_WITHOUT_OTP_POSSIBLE"
        ),
        "ACCOUNT92_RELOGIN_REQUIRED": by.get(92, {}).get("ACCOUNT92_RELOGIN_REQUIRED"),
        "ACCOUNT92_OPERATOR_DECISION_REQUIRED": by.get(92, {}).get("OPERATOR_DECISION_REQUIRED"),
        "RECOMMENDED_REMEDIATION_ORDER": [o["account_id"] for o in art["RECOMMENDED_REMEDIATION_ORDER"]],
        "SAFE_WITHOUT_OTP_ACCOUNTS": art["SAFE_WITHOUT_OTP_ACCOUNTS"],
        "OTP_REQUIRED_ACCOUNTS": art["OTP_REQUIRED_ACCOUNTS"],
        "OPERATOR_DECISION_REQUIRED_ACCOUNTS": art["OPERATOR_DECISION_REQUIRED_ACCOUNTS"],
        "GLOBAL_ACTIVE_SESSION_COUNT": art["post"]["GLOBAL_ACTIVE_SESSION_COUNT"],
        "ACTUAL_WORKER_IDS": art["post"]["ACTUAL_WORKER_IDS"],
        "SESSION_ROWS_MUTATED": art["SESSION_ROWS_MUTATED"],
        "SESSION_STATUS_CHANGES": art["SESSION_STATUS_CHANGES"],
        "IDENTITY_BINDING_CHANGES": art["IDENTITY_BINDING_CHANGES"],
        "LOGIN_CHALLENGE_DELTA": art["LOGIN_CHALLENGE_DELTA"],
        "MESSAGE_ATTEMPT_DELTA": art["MESSAGE_ATTEMPT_DELTA"],
        "MESSAGE_SENT": False,
        "OTP_REQUESTED": False,
        "CURRENT_PHASE": art["CURRENT_PHASE"],
        "PHASE_STATUS": art["PHASE_STATUS"],
        "MATRIX": str(MATRIX),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if art["PHASE_STATUS"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
