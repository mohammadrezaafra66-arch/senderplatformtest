"""L7 classification helpers for duplicate-session probe results (no promotion)."""

from __future__ import annotations

from typing import Any


def _is_valid(row: dict[str, Any]) -> bool:
    return (
        row.get("decrypt_status") == "OK"
        and row.get("structure_status") == "OK"
        and row.get("reconnect_status") == "AUTH_RECONNECT_PASS"
        and row.get("identity_match") is True
    )


def classify_duplicate_account(session_results: list[dict[str, Any]]) -> str:
    """Classify an account after per-session_id probes."""
    if not session_results:
        return "NO_VALID_SESSION"

    if all(r.get("decrypt_status") == "SESSION_DECRYPT_FAILED" for r in session_results):
        return "DECRYPT_REPAIR_OR_RELOGIN_REQUIRED"

    valid = [r for r in session_results if _is_valid(r)]
    others = [r for r in session_results if not _is_valid(r)]

    if not valid:
        return "NO_VALID_SESSION"

    if len(valid) >= 2:
        guids = {
            str(r.get("identity_guid") or "").strip()
            for r in valid
            if str(r.get("identity_guid") or "").strip()
        }
        if len(guids) > 1:
            return "IDENTITY_CONFLICT"
        return "BOTH_VALID_SAME_IDENTITY"

    # Exactly one proven valid session.
    for other in others:
        if other.get("identity_match") is False and other.get("decrypt_status") == "OK":
            return "IDENTITY_CONFLICT"
        if other.get("decrypt_status") == "SESSION_DECRYPT_FAILED":
            return "ONE_PROVEN_ONE_INVALID"
        if other.get("structure_status") == "SESSION_STRUCTURALLY_INVALID":
            return "ONE_PROVEN_ONE_INVALID"
        if other.get("reconnect_status") in {
            "AUTH_RECONNECT_FAILED",
            "AUTH_RECONNECT_TIMEOUT",
            "LOGIN_REQUIRED",
            "SESSION_STRUCTURALLY_INVALID",
            "SESSION_DECRYPT_FAILED",
        }:
            return "ONE_PROVEN_ONE_INVALID"
        if other.get("reconnect_status") in {"UNPROBED", "SKIPPED", None}:
            return "ONE_PROVEN_ONE_UNVERIFIED"
        if other.get("decrypt_status") in {"UNPROBED", None}:
            return "ONE_PROVEN_ONE_UNVERIFIED"
        return "ONE_PROVEN_ONE_INVALID"

    return "ONE_PROVEN_ONE_UNVERIFIED"


def classify_account12(session_results: list[dict[str, Any]]) -> str:
    """Account12 forensic classification — never invent a winner if both valid."""
    by_id = {
        int(r["session_id"]): r
        for r in session_results
        if r.get("session_id") is not None
    }
    s657 = by_id.get(657)
    s728 = by_id.get(728)
    if not s657 or not s728:
        return "STILL_AMBIGUOUS"

    v657 = _is_valid(s657)
    v728 = _is_valid(s728)
    if v657 and v728:
        g657 = str(s657.get("identity_guid") or "").strip()
        g728 = str(s728.get("identity_guid") or "").strip()
        if g657 and g728 and g657 != g728:
            return "IDENTITY_CONFLICT"
        return "BOTH_VALID_CURRENT_IDENTITY"
    if v728 and not v657:
        return "CLEAR_728_CANDIDATE"
    if v657 and not v728:
        return "CLEAR_657_CANDIDATE"
    if v657 or v728:
        return "ONE_INVALID_ONE_VALID"
    return "STILL_AMBIGUOUS"
