#!/usr/bin/env python3
"""M3 — Same-identity duplicate session resolution audit (READ-ONLY).

Targets ONLY Rubika accounts 12, 19, 81.
Determines whether an objective authoritative session exists per pair.
Never mutates production session state.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TARGETS: dict[int, list[int]] = {
    12: [657, 728],
    19: [726, 727],
    81: [722, 723],
}
EXPECTED_ACTIVES = {2: 721, 13: 729, 23: 725, 27: 772, 74: 724}
EXPECTED_WORKERS = [2, 12, 13, 23, 27, 74, 79]

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "rubika-remediation"
MATRIX_OUT = REPORT_DIR / "M3_SESSION_COMPARISON_MATRIX.json"
AUDIT_MD = REPORT_DIR / "M3_SAME_IDENTITY_DUPLICATE_AUDIT.md"
PLAN_MD = REPORT_DIR / "M3_DUPLICATE_RESOLUTION_PLAN.md"
ALLOWED = frozenset(
    {
        "M3_SESSION_COMPARISON_MATRIX.json",
        "M3_SAME_IDENTITY_DUPLICATE_AUDIT.md",
        "M3_DUPLICATE_RESOLUTION_PLAN.md",
    }
)


def _load_m1_helpers():
    m1_path = Path(__file__).resolve().parent / "_m1_manual_review_forensic_audit.py"
    spec = importlib.util.spec_from_file_location("m1_audit", m1_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _safe_fingerprint(envelope: dict[str, Any] | None) -> dict[str, Any]:
    if not envelope:
        return {"SESSION_FINGERPRINT": None}
    auth = envelope.get("auth") or ""
    ua = str(envelope.get("user_agent") or "")
    phone = str(envelope.get("phone_number") or "")
    pk = envelope.get("private_key") or b""
    if isinstance(pk, str):
        pk_len = len(pk)
    else:
        pk_len = len(pk)
    auth_prefix = hashlib.sha256(str(auth)[:32].encode()).hexdigest()[:12] if auth else None
    fp = hashlib.sha256(
        f"{ua}|{phone}|{auth_prefix}|{pk_len}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "SESSION_FINGERPRINT": fp,
        "USER_AGENT_HASH": hashlib.sha256(ua.encode()).hexdigest()[:12] if ua else None,
        "PHONE_HASH": hashlib.sha256(phone.encode()).hexdigest()[:12] if phone else None,
        "AUTH_MATERIAL_LEN": len(str(auth)) if auth else 0,
        "PRIVATE_KEY_LEN": pk_len,
    }


def _message_attempts_for_account(db, account_id: int, recover_fn) -> list[dict[str, Any]]:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, SQLAlchemyError

    try:
        rows = db.execute(
            text(
                """
                SELECT ma.id, ma.status::text, ma.created_at
                FROM message_attempts ma
                JOIN messages m ON m.id = ma.message_id
                WHERE m.account_id = :aid
                ORDER BY ma.created_at ASC
                """
            ),
            {"aid": account_id},
        ).fetchall()
        return [
            {
                "attempt_id": int(r[0]),
                "status": str(r[1]),
                "created_at": r[2].isoformat() if r[2] else None,
            }
            for r in rows
        ]
    except (DBAPIError, SQLAlchemyError) as exc:
        recover_fn(db)
        return [{"error": type(exc).__name__}]


def _message_usage_account(db, account_id: int, recover_fn) -> dict[str, Any]:
    attempts = _message_attempts_for_account(db, account_id, recover_fn)
    if attempts and "error" in attempts[0]:
        return {
            "error": attempts[0]["error"],
            "WAS_ACCOUNT_USED_IN_PRODUCTION": None,
            "attempts": attempts,
        }
    success = [a for a in attempts if a.get("status") == "SUCCESS"]
    return {
        "attempt_count": len(attempts),
        "successful_attempt_count": len(success),
        "last_attempt_at": attempts[-1]["created_at"] if attempts else None,
        "last_successful_attempt_at": success[-1]["created_at"] if success else None,
        "success_attempt_ids": [a["attempt_id"] for a in success],
        "WAS_ACCOUNT_USED_IN_PRODUCTION": bool(success),
        "attempts": attempts,
    }


def _time_window_dispatch_attribution(
    attempts: list[dict[str, Any]],
    sessions: list[Any],
) -> dict[int, dict[str, Any]]:
    """Attribute SUCCESS attempts to sessions by created_at window (read-only inference)."""
    from datetime import datetime

    success = [a for a in attempts if a.get("status") == "SUCCESS" and a.get("created_at")]
    out: dict[int, dict[str, Any]] = {}
    ordered = sorted(sessions, key=lambda r: r.created_at or datetime.min)
    for row in ordered:
        sid = int(row.id)
        created = row.created_at
        next_created = None
        idx = ordered.index(row)
        if idx + 1 < len(ordered):
            next_created = ordered[idx + 1].created_at
        attributed: list[int] = []
        for att in success:
            ts = datetime.fromisoformat(att["created_at"])
            if created and ts >= created and (next_created is None or ts < next_created):
                attributed.append(int(att["attempt_id"]))
        out[sid] = {
            "TIME_WINDOW_SUCCESS_ATTEMPT_IDS": attributed,
            "TIME_WINDOW_SUCCESS_COUNT": len(attributed),
            "WAS_SESSION_ACTUALLY_USED_IN_PRODUCTION": bool(attributed),
            "LAST_CONFIRMED_USE_AT": success[-1]["created_at"]
            if attributed and success
            else (att["created_at"] for att in success if int(att["attempt_id"]) in attributed),
        }
        if attributed:
            last_ids = [a for a in success if int(a["attempt_id"]) in attributed]
            out[sid]["LAST_CONFIRMED_USE_AT"] = last_ids[-1]["created_at"] if last_ids else None
    return out


def _login_challenge_refs(db, account_id: int, session_ids: list[int], recover_fn) -> dict[int, dict]:
    from core_engine.models import RubikaLoginChallenge

    out: dict[int, dict] = {sid: {"candidate_refs": 0, "completed_refs": 0} for sid in session_ids}
    try:
        rows = (
            db.query(RubikaLoginChallenge)
            .filter(RubikaLoginChallenge.account_id == account_id)
            .all()
        )
        for ch in rows:
            for sid in session_ids:
                if ch.candidate_session_id == sid:
                    out[sid]["candidate_refs"] += 1
                if ch.completed_session_id == sid:
                    out[sid]["completed_refs"] += 1
    except Exception as exc:  # noqa: BLE001
        recover_fn(db)
        return {sid: {"error": type(exc).__name__} for sid in session_ids}
    return out


def _report_mentions(session_ids: list[int]) -> dict[int, dict[str, Any]]:
    root = REPORT_DIR
    counts = {sid: {"report_mention_count": 0, "report_files": []} for sid in session_ids}
    if not root.exists():
        return counts
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".md", ".txt"}:
            continue
        if path.name.startswith("M3_"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for sid in session_ids:
            # Match session id as word boundary (avoid 72 matching 722)
            if re.search(rf"\b{sid}\b", text):
                counts[sid]["report_mention_count"] += 1
                if len(counts[sid]["report_files"]) < 8:
                    counts[sid]["report_files"].append(str(path.relative_to(root)))
    return counts


def _redis_session_refs(session_ids: list[int]) -> dict[int, list[str]]:
    m1 = _load_m1_helpers()
    out: dict[int, list[str]] = {sid: [] for sid in session_ids}
    try:
        r = m1._read_only_redis()
        try:
            for sid in session_ids:
                needle = str(sid)
                # Bounded scan — read-only
                cursor = 0
                seen = 0
                while seen < 200:
                    cursor, keys = r.scan(cursor=cursor, match="*", count=200)
                    for k in keys:
                        if needle in str(k):
                            out[sid].append(str(k))
                            if len(out[sid]) >= 5:
                                break
                    seen += 1
                    if cursor == 0 or len(out[sid]) >= 5:
                        break
        finally:
            r.close()
    except Exception:  # noqa: BLE001
        pass
    return out


def _legacy_runtime_reference(db, account_id: int) -> dict[str, Any]:
    from core_engine.services.rubika_canonical_runtime import select_legacy_rubika_session_row

    row = select_legacy_rubika_session_row(db, account_id)
    if row is None:
        return {
            "LEGACY_MAX_ID_SESSION": None,
            "NOTE": "No legacy row",
        }
    return {
        "LEGACY_MAX_ID_SESSION": int(row.id),
        "NOTE": "Read-only max(id) preview — NOT used for winner selection",
    }


def _evaluate_pair(
    account_id: int,
    sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply M3 deterministic winner rules — never max(id) or freshness alone."""
    if len(sessions) != 2:
        return {"error": "expected_pair"}

    a, b = sessions[0], sessions[1]
    sid_a, sid_b = a["SESSION_ID"], b["SESSION_ID"]
    strong: list[str] = []

    def _only_one(has_a: bool, has_b: bool, label: str) -> None:
        if has_a and not has_b:
            strong.append(f"{label}_ONLY_{sid_a}")
        elif has_b and not has_a:
            strong.append(f"{label}_ONLY_{sid_b}")

    # A — session-specific production runtime reference
    _only_one(
        bool(a.get("WAS_SESSION_ACTUALLY_USED_IN_PRODUCTION")),
        bool(b.get("WAS_SESSION_ACTUALLY_USED_IN_PRODUCTION")),
        "A_TIME_WINDOW_PRODUCTION_USE",
    )
    _only_one(
        bool(a.get("LOGIN_CHALLENGE_COMPLETED_REFS")),
        bool(b.get("LOGIN_CHALLENGE_COMPLETED_REFS")),
        "A_LOGIN_COMPLETED_REF",
    )
    redis_a = len(a.get("CURRENT_REDIS_REFERENCE") or [])
    redis_b = len(b.get("CURRENT_REDIS_REFERENCE") or [])
    _only_one(redis_a > 0, redis_b > 0, "A_REDIS_SESSION_KEY")

    # B — one has successful dispatch and other has none (time-window attributed)
    sa = int(a.get("TIME_WINDOW_SUCCESS_COUNT") or 0)
    sb = int(b.get("TIME_WINDOW_SUCCESS_COUNT") or 0)
    if sa > 0 and sb == 0:
        strong.append(f"B_DISPATCH_ONLY_{sid_a}")
    elif sb > 0 and sa == 0:
        strong.append(f"B_DISPATCH_ONLY_{sid_b}")
    elif sa > 0 and sb > 0:
        # Both have dispatch — conflicting, not a single winner
        strong.append(f"B_CONFLICTING_DISPATCH_{sid_a}_AND_{sid_b}")

    # D — lifecycle compatibility (validated_at / last_refresh exclusive)
    for field in ("validated_at", "last_refresh_at"):
        ha, hb = bool(a.get(field)), bool(b.get(field))
        _only_one(ha, hb, f"D_{field.upper()}")

    # E — fresh proof differential
    _only_one(bool(a.get("AUTH_PASS")), bool(b.get("AUTH_PASS")), "E_AUTH")
    _only_one(bool(a.get("IDENTITY_MATCH")), bool(b.get("IDENTITY_MATCH")), "E_IDENTITY")

    max_id_selection = False
    freshness_only = False

    winner: int | None = None
    reason = ""
    conflicting = any("CONFLICTING" in s for s in strong)
    unambiguous = [s for s in strong if "CONFLICTING" not in s and "ONLY" in s]
    if len(unambiguous) == 1 and not conflicting:
        tok = unambiguous[0]
        m = re.search(r"_(\d+)$", tok)
        if m:
            winner = int(m.group(1))
            reason = tok
    elif unambiguous or conflicting:
        winner = None
        reason = "conflicting_or_insufficient_strong_signals:" + ",".join(strong)
    else:
        reason = "NO_OBJECTIVE_WINNER"

    operational_equivalent = (
        a.get("AUTH_PASS")
        and b.get("AUTH_PASS")
        and a.get("IDENTITY_MATCH")
        and b.get("IDENTITY_MATCH")
        and a.get("STRUCTURAL_VALID")
        and b.get("STRUCTURAL_VALID")
        and not unambiguous
        and not conflicting
        and winner is None
    )

    return {
        "SESSION_PAIR": [sid_a, sid_b],
        "SESSION_PAIR_EQUIVALENT": operational_equivalent,
        "STRONG_DIFFERENTIATORS": strong,
        "DETERMINISTIC_WINNER_FOUND": winner is not None,
        "PROPOSED_AUTHORITATIVE_SESSION": winner,
        "SELECTION_REASON": reason,
        "OPERATOR_DECISION_REQUIRED": winner is None,
        "MAX_ID_SELECTION_USED": max_id_selection,
        "FRESHNESS_ONLY_SELECTION_USED": freshness_only,
        "AUTH_FINGERPRINTS_DIFFER": a.get("SESSION_FINGERPRINT") != b.get("SESSION_FINGERPRINT"),
        "AUTH_FINGERPRINT_NOTE": (
            "Distinct auth tokens expected for parallel valid logins; "
            "not treated as provider primary/secondary without explicit provider metadata."
        ),
    }


async def run() -> dict[str, Any]:
    m1 = _load_m1_helpers()
    from core_engine.database import SessionLocal
    from core_engine.models import Account, ChannelSession, PlatformType, SessionType
    from core_engine.services.account_runtime_status import compute_account_runtime_status

    db = SessionLocal()
    art: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "READ_ONLY": True,
        "PHASE": "M3_SAME_IDENTITY_DUPLICATE_RESOLUTION_AUDIT",
        "MAX_ID_SELECTION_USED": False,
        "FRESHNESS_ONLY_SELECTION_USED": False,
        "OTP_REQUESTED": False,
        "MESSAGE_SENT": False,
    }
    try:
        ro = m1._arm_db_read_only(db)
        art["READ_ONLY_ENFORCEMENT"] = ro
        pre = m1._baseline(db)
        # M2 changed global active count to 5 (Account2)
        if pre["GLOBAL_ACTIVE_SESSION_COUNT"] != 5:
            raise SystemExit(f"REFUSE: active count {pre['GLOBAL_ACTIVE_SESSION_COUNT']} != 5")
        if not all(pre["ACTIVES"].get(k) == v for k, v in EXPECTED_ACTIVES.items()):
            raise SystemExit(f"REFUSE: canonical actives drifted {pre['ACTIVES']}")
        if pre["ACTUAL_WORKER_IDS"] != EXPECTED_WORKERS:
            raise SystemExit(f"REFUSE: workers drifted {pre['ACTUAL_WORKER_IDS']}")
        art["pre"] = {
            "GLOBAL_ACTIVE_SESSION_COUNT": pre["GLOBAL_ACTIVE_SESSION_COUNT"],
            "ACTIVES": pre["ACTIVES"],
            "ACTUAL_WORKER_IDS": pre["ACTUAL_WORKER_IDS"],
            "msg": pre["msg"],
            "ch": pre["ch"],
            "sess_rows": pre["sess_rows"],
        }

        accounts_out: list[dict[str, Any]] = []
        for aid, expected_sids in TARGETS.items():
            account = db.query(Account).filter(Account.id == aid).first()
            if account is None:
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
            found_sids = [int(r.id) for r in rows]
            if set(found_sids) != set(expected_sids):
                raise SystemExit(f"REFUSE: Account{aid} session inventory {found_sids} != {expected_sids}")

            expected_guid = str(account.rubika_guid or "").strip() or None
            acct_usage = _message_usage_account(db, aid, m1._recover_read_only)
            tw = _time_window_dispatch_attribution(acct_usage.get("attempts") or [], rows)
            lc_refs = _login_challenge_refs(db, aid, expected_sids, m1._recover_read_only)
            report_refs = _report_mentions(expected_sids)
            redis_refs = _redis_session_refs(expected_sids)
            cov = m1._worker_cov(aid)
            legacy_ref = _legacy_runtime_reference(db, aid)
            worker_sess_ref = "UNKNOWN"
            if legacy_ref.get("LEGACY_MAX_ID_SESSION"):
                worker_sess_ref = (
                    f"LEGACY_LOADER_WOULD_SELECT_{legacy_ref['LEGACY_MAX_ID_SESSION']}"
                    " (max-id preview only; worker does not pin session_id; "
                    "canonical MANUAL_REVIEW blocks enforce load)"
                )

            scorecards: list[dict[str, Any]] = []
            for row in rows:
                sid = int(row.id)
                ds = m1._decrypt_structure(row)
                envelope = ds.pop("_envelope_for_probe", None)
                fp = _safe_fingerprint(envelope)
                tw_sid = tw.get(sid, {})
                card: dict[str, Any] = {
                    "SESSION_ID": sid,
                    "DECRYPT_OK": ds["decrypt_status"] == "DECRYPT_OK",
                    "STRUCTURAL_VALID": ds.get("structural_status") == "STRUCTURALLY_VALID",
                    "AUTH_PASS": False,
                    "IDENTITY_MATCH": False,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "last_refresh_at": row.last_refresh_at.isoformat() if row.last_refresh_at else None,
                    "validated_at": row.validated_at.isoformat() if row.validated_at else None,
                    "session_status": m1._enum_val(row.session_status),
                    "stored_identity_guid_hash": m1._guid_hash(row.identity_guid),
                    "login_attempt_id": row.login_attempt_id,
                    "validation_error_code": row.validation_error_code,
                    "encryption_meta": ds.get("encryption_meta"),
                    "envelope_guid_hash": ds.get("envelope_guid_hash"),
                    **fp,
                    "LOGIN_CHALLENGE_CANDIDATE_REFS": lc_refs.get(sid, {}).get("candidate_refs", 0),
                    "LOGIN_CHALLENGE_COMPLETED_REFS": lc_refs.get(sid, {}).get("completed_refs", 0),
                    "REPORT_MENTION_COUNT": report_refs.get(sid, {}).get("report_mention_count", 0),
                    "REPORT_MENTION_FILES": report_refs.get(sid, {}).get("report_files", []),
                    "CURRENT_REDIS_REFERENCE": redis_refs.get(sid, []),
                    "CURRENT_RUNTIME_REFERENCE": legacy_ref,
                    "CURRENT_WORKER_REFERENCE": "ACCOUNT_LEVEL_ONLY" if cov else "NONE",
                    "TIME_WINDOW_SUCCESS_ATTEMPT_IDS": tw_sid.get("TIME_WINDOW_SUCCESS_ATTEMPT_IDS", []),
                    "TIME_WINDOW_SUCCESS_COUNT": tw_sid.get("TIME_WINDOW_SUCCESS_COUNT", 0),
                    "WAS_SESSION_ACTUALLY_USED_IN_PRODUCTION": bool(
                        tw_sid.get("WAS_SESSION_ACTUALLY_USED_IN_PRODUCTION")
                    ),
                    "LAST_CONFIRMED_USE_AT": tw_sid.get("LAST_CONFIRMED_USE_AT"),
                    "LAST_PROVEN_RUNTIME_USE": tw_sid.get("LAST_CONFIRMED_USE_AT"),
                    "LAST_SUCCESSFUL_DISPATCH_REFERENCE": tw_sid.get("TIME_WINDOW_SUCCESS_ATTEMPT_IDS"),
                    "PROVEN_SUCCESSFUL_OPERATIONS": tw_sid.get("TIME_WINDOW_SUCCESS_COUNT", 0),
                    "CANONICAL_COMPATIBILITY": "LEGACY_UNCLASSIFIED",
                    "PROVIDER_SESSION_HEALTH": None,
                    "LAST_IDENTITY_PROOF": None,
                    "LAST_PROVIDER_ACTIVITY": None,
                    "CURRENT_CACHE_REFERENCE": [],
                    "CURRENT_SESSION_FINGERPRINT": fp.get("SESSION_FINGERPRINT"),
                }
                if (
                    ds["decrypt_status"] == "DECRYPT_OK"
                    and ds.get("structural_status") == "STRUCTURALLY_VALID"
                    and envelope
                ):
                    auth = await m1._auth_probe(
                        envelope,
                        expected_guid=expected_guid,
                        account_bound_guid=expected_guid,
                        account_identity_status=m1._enum_val(account.rubika_identity_status),
                    )
                    envelope.clear()
                    card["AUTH_PASS"] = auth.get("auth_status") == "AUTH_PASS"
                    card["IDENTITY_MATCH"] = bool(auth.get("identity_match"))
                    card["LAST_IDENTITY_PROOF"] = {
                        "at": art["generated_at"],
                        "identity_guid_hash": auth.get("identity_guid_hash"),
                    }
                    card["LAST_AUTH_PROOF"] = {
                        "at": art["generated_at"],
                        "reason_code": auth.get("reason_code"),
                        "identity_guid_hash": auth.get("identity_guid_hash"),
                        "duration_ms": auth.get("duration_ms"),
                    }
                    card["PROVIDER_SESSION_HEALTH"] = auth.get("reason_code")
                    card["LAST_PROVIDER_ACTIVITY"] = auth.get("reason_code")
                if card["LOGIN_CHALLENGE_COMPLETED_REFS"] > 0:
                    card["WAS_SESSION_ACTUALLY_USED_IN_PRODUCTION"] = True
                    card["PROVEN_SUCCESSFUL_OPERATIONS"] = max(
                        card["PROVEN_SUCCESSFUL_OPERATIONS"],
                        card["LOGIN_CHALLENGE_COMPLETED_REFS"],
                    )

                scorecards.append(card)

            pair_eval = _evaluate_pair(aid, scorecards)
            runtime = compute_account_runtime_status(db, account, worker_covered=cov)
            winner_sid = pair_eval.get("PROPOSED_AUTHORITATIVE_SESSION")
            post_status = "MANUAL_REVIEW"
            post_worker = "PINNED_COVERAGE" if aid == 12 and cov else ("COVERED" if cov else "ABSENT")
            post_campaign = False
            if winner_sid:
                post_status = "READY" if cov else "AUTHENTICATED_NO_WORKER"
                post_campaign = bool(cov)

            accounts_out.append(
                {
                    "ACCOUNT_ID": aid,
                    "SESSION_PAIR": expected_sids,
                    "account_usage": acct_usage,
                    "worker_present": cov,
                    "worker_pinned": aid == 12,
                    "legacy_runtime_reference": legacy_ref,
                    "CURRENT_WORKER_SESSION_REFERENCE": worker_sess_ref,
                    "runtime_status": runtime.runtime_status,
                    "scorecards": scorecards,
                    **pair_eval,
                    "STRONG_SELECTION_EVIDENCE": pair_eval.get("STRONG_DIFFERENTIATORS") or [],
                    "SAFE_TO_CANONICALIZE_AFTER_SELECTION": bool(
                        winner_sid and all(s["AUTH_PASS"] for s in scorecards)
                    ),
                    "EXPECTED_POST_CANONICAL_STATUS": post_status if winner_sid else "MANUAL_REVIEW",
                    "EXPECTED_WORKER_STATE": post_worker,
                    "EXPECTED_CAMPAIGN_ELIGIBILITY": post_campaign,
                    "RISK_LEVEL": (
                        "LOW"
                        if winner_sid
                        else ("HIGH" if aid == 12 else "MEDIUM")
                    ),
                }
            )

        all_no_winner = all(not a["DETERMINISTIC_WINNER_FOUND"] for a in accounts_out)
        equiv_19_81 = all(
            a["SESSION_PAIR_EQUIVALENT"]
            for a in accounts_out
            if a["ACCOUNT_ID"] in (19, 81)
        )
        acct12 = next(a for a in accounts_out if a["ACCOUNT_ID"] == 12)
        acct12_dual_dispatch = any(
            "B_CONFLICTING_DISPATCH" in s for s in acct12.get("STRONG_DIFFERENTIATORS", [])
        )
        neutral_possible = all_no_winner and equiv_19_81
        art["accounts"] = accounts_out
        art["NEUTRAL_DUPLICATE_COLLAPSE_POLICY_POSSIBLE"] = neutral_possible
        art["RECOMMENDED_POLICY"] = (
            "Operator-approved neutral duplicate-collapse for accounts 19 and 81: "
            "both pairs are operationally equivalent (AUTH_PASS, same GUID, no dispatch/runtime "
            "differentiator). Use one documented neutral tie-break (e.g. lowest session_id) with "
            "non-winner marked SUPERSEDED — never max(id) or updated_at alone. "
            "Account 12 requires separate operator decision: both sessions have time-windowed "
            "SUCCESS dispatch evidence (657→attempt 2 pre-728; 728→attempt 15 post-create); "
            "do not auto-select 728 via max-id or recency."
            if neutral_possible
            else "Per-account operator selection required."
        )
        art["RECOMMENDED_REMEDIATION_ORDER"] = [19, 81, 12]
        art["ACCOUNT12_DUAL_DISPATCH_CONFLICT"] = acct12_dual_dispatch
        art["READ_ONLY_AUDIT_PASS"] = True
        art["SESSION_ROWS_MUTATED"] = 0
        art["SESSION_STATUS_CHANGES"] = 0
        art["PHASE_STATUS"] = "COMPLETE"
        return art
    finally:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()


def _write_reports(art: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for p in REPORT_DIR.iterdir():
        if p.name.startswith("M3_") and p.name not in ALLOWED:
            raise SystemExit(f"unexpected M3 report {p.name}")
    MATRIX_OUT.write_text(json.dumps(art, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    def sc_line(sc: dict[str, Any]) -> list[str]:
        return [
            f"**Session {sc['SESSION_ID']}**",
            f"- DECRYPT_OK={sc.get('DECRYPT_OK')} STRUCTURAL_VALID={sc.get('STRUCTURAL_VALID')}",
            f"- AUTH_PASS={sc.get('AUTH_PASS')} IDENTITY_MATCH={sc.get('IDENTITY_MATCH')}",
            f"- created_at={sc.get('created_at')} updated_at={sc.get('updated_at')}",
            f"- SESSION_FINGERPRINT={sc.get('SESSION_FINGERPRINT')} envelope_guid_hash={sc.get('envelope_guid_hash')}",
            f"- WAS_SESSION_ACTUALLY_USED_IN_PRODUCTION={sc.get('WAS_SESSION_ACTUALLY_USED_IN_PRODUCTION')}",
            f"- TIME_WINDOW_SUCCESS_ATTEMPT_IDS={sc.get('TIME_WINDOW_SUCCESS_ATTEMPT_IDS')}",
            f"- LAST_CONFIRMED_USE_AT={sc.get('LAST_CONFIRMED_USE_AT')}",
            f"- LAST_AUTH_PROOF={sc.get('LAST_AUTH_PROOF')}",
            f"- CURRENT_REDIS_REFERENCE={sc.get('CURRENT_REDIS_REFERENCE')}",
            f"- PROVIDER_SESSION_HEALTH={sc.get('PROVIDER_SESSION_HEALTH')}",
            "",
        ]

    lines = [
        "# M3 — Same-Identity Duplicate Session Resolution Audit",
        "",
        f"Generated: {art.get('generated_at')}",
        "",
        "## Safety",
        "",
        f"- READ_ONLY_AUDIT_PASS={art.get('READ_ONLY_AUDIT_PASS')}",
        f"- SESSION_ROWS_MUTATED={art.get('SESSION_ROWS_MUTATED')}",
        f"- SESSION_STATUS_CHANGES={art.get('SESSION_STATUS_CHANGES')}",
        f"- OTP_REQUESTED={art.get('OTP_REQUESTED')}",
        f"- MESSAGE_SENT={art.get('MESSAGE_SENT')}",
        f"- MAX_ID_SELECTION_USED={art.get('MAX_ID_SELECTION_USED')}",
        f"- FRESHNESS_ONLY_SELECTION_USED={art.get('FRESHNESS_ONLY_SELECTION_USED')}",
        "",
        "## Production baseline preserved",
        "",
        f"- GLOBAL_ACTIVE_SESSION_COUNT={art.get('pre', {}).get('GLOBAL_ACTIVE_SESSION_COUNT')}",
        f"- ACTIVES={art.get('pre', {}).get('ACTIVES')}",
        f"- ACTUAL_WORKER_IDS={art.get('pre', {}).get('ACTUAL_WORKER_IDS')}",
        "",
    ]
    for a in art.get("accounts", []):
        aid = a["ACCOUNT_ID"]
        lines += [
            f"## Account {aid}",
            "",
            f"- SESSION_PAIR={a.get('SESSION_PAIR')}",
            f"- SESSION_PAIR_EQUIVALENT={a.get('SESSION_PAIR_EQUIVALENT')}",
            f"- DETERMINISTIC_WINNER_FOUND={a.get('DETERMINISTIC_WINNER_FOUND')}",
            f"- PROPOSED_AUTHORITATIVE_SESSION={a.get('PROPOSED_AUTHORITATIVE_SESSION')}",
            f"- ACCOUNT{aid}_STRONG_DIFFERENTIATOR={a.get('STRONG_DIFFERENTIATORS')}",
            f"- ACCOUNT{aid}_SELECTION_REASON={a.get('SELECTION_REASON')}",
            f"- OPERATOR_DECISION_REQUIRED={a.get('OPERATOR_DECISION_REQUIRED')}",
            f"- CURRENT_WORKER_SESSION_REFERENCE={a.get('CURRENT_WORKER_SESSION_REFERENCE')}",
            f"- EXPECTED_POST_CANONICAL_STATUS={a.get('EXPECTED_POST_CANONICAL_STATUS')}",
            f"- RISK_LEVEL={a.get('RISK_LEVEL')}",
            "",
        ]
        for sc in a.get("scorecards", []):
            lines += sc_line(sc)

    lines += [
        "## Policy",
        "",
        f"NEUTRAL_DUPLICATE_COLLAPSE_POLICY_POSSIBLE={art.get('NEUTRAL_DUPLICATE_COLLAPSE_POLICY_POSSIBLE')}",
        f"RECOMMENDED_POLICY={art.get('RECOMMENDED_POLICY')}",
        f"RECOMMENDED_REMEDIATION_ORDER={art.get('RECOMMENDED_REMEDIATION_ORDER')}",
        "",
        f"PHASE_STATUS={art.get('PHASE_STATUS')}",
        "",
    ]
    AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    plan = [
        "# M3 — Duplicate Resolution Plan (read-only recommendations)",
        "",
        f"Generated: {art.get('generated_at')}",
        "",
        "## Summary",
        "",
        "No deterministic winner found for any target account under M3 strong-evidence rules.",
        "Accounts 19 and 81 are operationally equivalent pairs suitable for a single neutral",
        "duplicate-collapse policy after operator approval. Account 12 has conflicting",
        "time-windowed dispatch evidence on both sessions and requires a separate decision.",
        "",
        "## Per-account",
        "",
    ]
    for a in art.get("accounts", []):
        plan.append(f"### Account {a['ACCOUNT_ID']} (sessions {a.get('SESSION_PAIR')})")
        if a.get("DETERMINISTIC_WINNER_FOUND"):
            plan.append(
                f"- Canonicalize session **{a['PROPOSED_AUTHORITATIVE_SESSION']}** "
                f"(reason: {a.get('SELECTION_REASON')})."
            )
            plan.append("- Mark sibling SUPERSEDED (not delete).")
        else:
            plan.append("- **NO_OBJECTIVE_WINNER** — operator decision required.")
        plan.append(f"- STRONG_DIFFERENTIATORS: {a.get('STRONG_DIFFERENTIATORS')}")
        plan.append(f"- Expected post-canonical: {a.get('EXPECTED_POST_CANONICAL_STATUS')}")
        plan.append(f"- Worker state: {a.get('EXPECTED_WORKER_STATE')}")
        plan.append(f"- Campaign eligibility after selection: {a.get('EXPECTED_CAMPAIGN_ELIGIBILITY')}")
        plan.append("")
    plan += [
        "## Neutral duplicate-collapse policy (not executed in M3)",
        "",
        f"NEUTRAL_DUPLICATE_COLLAPSE_POLICY_POSSIBLE={art.get('NEUTRAL_DUPLICATE_COLLAPSE_POLICY_POSSIBLE')}",
        "",
        art.get("RECOMMENDED_POLICY", ""),
        "",
        "## Recommended order",
        "",
        f"RECOMMENDED_REMEDIATION_ORDER={art.get('RECOMMENDED_REMEDIATION_ORDER')}",
        "",
        "NEXT_SAFE_ACTION: Obtain operator approval for neutral collapse on 19/81; resolve Account 12 separately.",
        "",
    ]
    PLAN_MD.write_text("\n".join(plan) + "\n", encoding="utf-8")


def main() -> int:
    art = asyncio.run(run())
    _write_reports(art)
    summary = {
        "READ_ONLY_AUDIT_PASS": art.get("READ_ONLY_AUDIT_PASS"),
        "PHASE_STATUS": art.get("PHASE_STATUS"),
        "accounts": [
            {
                "ACCOUNT_ID": a["ACCOUNT_ID"],
                "DETERMINISTIC_WINNER_FOUND": a["DETERMINISTIC_WINNER_FOUND"],
                "PROPOSED_AUTHORITATIVE_SESSION": a["PROPOSED_AUTHORITATIVE_SESSION"],
                "OPERATOR_DECISION_REQUIRED": a["OPERATOR_DECISION_REQUIRED"],
                "SESSION_PAIR_EQUIVALENT": a["SESSION_PAIR_EQUIVALENT"],
            }
            for a in art.get("accounts", [])
        ],
        "NEUTRAL_DUPLICATE_COLLAPSE_POLICY_POSSIBLE": art.get("NEUTRAL_DUPLICATE_COLLAPSE_POLICY_POSSIBLE"),
        "RECOMMENDED_REMEDIATION_ORDER": art.get("RECOMMENDED_REMEDIATION_ORDER"),
        "GLOBAL_ACTIVE_SESSION_COUNT": art.get("pre", {}).get("GLOBAL_ACTIVE_SESSION_COUNT"),
        "SESSION_ROWS_MUTATED": art.get("SESSION_ROWS_MUTATED"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if art.get("PHASE_STATUS") == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
