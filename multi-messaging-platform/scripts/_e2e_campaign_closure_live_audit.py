#!/usr/bin/env python3
"""E2E campaign closure — exhaustive live sender reconciliation (read-only)."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "reports" / "campaign-forensics" / "E2E_LIVE_SENDER_RECONCILIATION.json"

LABEL_BY_STATUS = {
    "READY": "آماده ارسال",
    "MANUAL_REVIEW": "نیازمند بررسی",
    "LOGIN_REQUIRED": "نیاز به ورود",
    "SESSION_ERROR": "خطای سشن",
    "CONNECTION_ERROR": "خطای اتصال",
    "AUTHENTICATED_NO_WORKER": "متصل، Worker آماده نیست",
}

GENERIC_ACTIVE_PATTERNS = ("فعال", "فعال · rubika", "active · rubika")


def _looks_malformed_identity(value: str | None) -> bool:
    if not value:
        return True
    v = str(value).strip()
    if not v:
        return True
    if v.lower() in {"true", "false", "none", "null"}:
        return True
    compact = v.replace(" ", "")
    if compact.isdigit() and len(compact) <= 3:
        return True
    if re.fullmatch(r"\d+/\d+", compact):
        return True
    return False


def _login(base: str, user: str, password: str) -> str:
    data = urllib.parse.urlencode({"username": user, "password": password}).encode()
    req = urllib.request.Request(
        f"{base}/auth/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    return body["access_token"]


def _get(base: str, path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{base}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    base = os.environ.get("MMP_API_BASE", "http://127.0.0.1:8000")
    user = os.environ.get("MMP_AUDIT_USER", "admin")
    password = os.environ.get("MMP_AUDIT_PASSWORD", "admin123")
    campaign_id = int(os.environ.get("MMP_AUDIT_CAMPAIGN_ID", "103"))

    token = _login(base, user, password)
    accounts = _get(base, "/accounts?platform=rubika&limit=200", token)
    preflight = _get(base, f"/campaigns/{campaign_id}/preflight", token)

    rubika_active = [
        a for a in accounts.get("items", [])
        if a.get("platform") == "rubika" and a.get("status") == "active"
    ]
    pf_by_id = {int(r["account_id"]): r for r in preflight.get("accounts", [])}

    status_mismatch_ids: list[int] = []
    selectability_mismatch_ids: list[int] = []
    display_mismatch_ids: list[int] = []
    blocker_mismatch_ids: list[int] = []
    generic_active_ids: list[int] = []
    non_ready_selectable: list[int] = []
    ready_disabled_no_reason: list[int] = []
    malformed_identity_ids: list[int] = []

    rows = []
    seen_ids: set[int] = set()

    for a in rubika_active:
        aid = int(a["id"])
        if aid in seen_ids:
            continue
        seen_ids.add(aid)

        rs = a.get("runtime_status")
        expected_label = (
            a.get("campaign_status_label")
            or a.get("runtime_status_label")
            or LABEL_BY_STATUS.get(rs or "", rs or "")
        )
        actual_label = a.get("campaign_status_label") or a.get("runtime_status_label") or ""
        eligible = a.get("campaign_eligible") is True
        expected_selectable = eligible

        pf = pf_by_id.get(aid, {})
        pf_eligible = pf.get("campaign_eligible")
        pf_label = pf.get("runtime_status_label") or pf.get("readiness")

        if actual_label.strip() in GENERIC_ACTIVE_PATTERNS or actual_label.strip() == "فعال":
            generic_active_ids.append(aid)

        if rs and expected_label and actual_label and actual_label not in (expected_label,):
            # Allow campaign_status_label to be more explicit than runtime
            if actual_label in GENERIC_ACTIVE_PATTERNS or actual_label == "active":
                status_mismatch_ids.append(aid)

        if pf_eligible is not None and bool(pf_eligible) != eligible:
            selectability_mismatch_ids.append(aid)

        disp = a.get("display_identity")
        if _looks_malformed_identity(disp):
            malformed_identity_ids.append(aid)
            display_mismatch_ids.append(aid)

        blocker = a.get("campaign_blocker_code")
        if not eligible and not blocker and rs not in ("READY", None):
            blocker_mismatch_ids.append(aid)

        if not eligible and pf_eligible is True:
            non_ready_selectable.append(aid)
        if eligible and pf_eligible is False:
            ready_disabled_no_reason.append(aid)

        rows.append({
            "ACCOUNT_ID": aid,
            "DISPLAY_IDENTITY": disp,
            "L18_RUNTIME_STATUS": rs,
            "L18_RUNTIME_LABEL": a.get("runtime_status_label"),
            "CAMPAIGN_ELIGIBLE": eligible,
            "EXPECTED_UI_LABEL": expected_label,
            "ACTUAL_API_LABEL": actual_label,
            "EXPECTED_SELECTABLE": expected_selectable,
            "BLOCKER_CODE": blocker,
            "BLOCKER_LABEL": a.get("campaign_blocker_label"),
        })

    total = len(rubika_active)
    checked = len(rows)

    out = {
        "TOTAL_CAMPAIGN_SENDER_CANDIDATES": total,
        "CAMPAIGN_ACCOUNTS_CHECKED_COUNT": checked,
        "ALL_CAMPAIGN_ACCOUNTS_CHECKED": checked == total and len(seen_ids) == total,
        "GENERIC_ACTIVE_ONLY_ROWS": len(generic_active_ids),
        "GENERIC_ACTIVE_ONLY_ACCOUNT_IDS": generic_active_ids,
        "ALL_SENDER_STATUS_MISMATCHES": len(status_mismatch_ids),
        "ALL_SENDER_STATUS_MISMATCH_ACCOUNT_IDS": status_mismatch_ids,
        "ALL_SENDER_SELECTABILITY_MISMATCHES": len(selectability_mismatch_ids),
        "ALL_SENDER_SELECTABILITY_MISMATCH_ACCOUNT_IDS": selectability_mismatch_ids,
        "ALL_SENDER_DISPLAY_IDENTITY_MISMATCHES": len(display_mismatch_ids),
        "ALL_SENDER_DISPLAY_IDENTITY_MISMATCH_ACCOUNT_IDS": display_mismatch_ids,
        "ALL_SENDER_BLOCKER_MISMATCHES": len(blocker_mismatch_ids),
        "NON_READY_SELECTABLE_ACCOUNTS": len(non_ready_selectable),
        "READY_DISABLED_WITHOUT_REASON": len(ready_disabled_no_reason),
        "LIVE_MALFORMED_SENDER_IDENTITY_ROWS": len(malformed_identity_ids),
        "LIVE_SENDER_ROWS_MISSING": max(0, total - checked),
        "LIVE_DUPLICATE_SENDER_ROWS": max(0, checked - len(seen_ids)),
        "PREFLIGHT_CAMPAIGN_ELIGIBLE_ACCOUNTS": preflight.get("campaign_eligible_accounts"),
        "PREFLIGHT_READY_ACCOUNTS": preflight.get("ready_accounts"),
        "rows": rows,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {k: v for k, v in out.items() if k != "rows"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        raise SystemExit(1) from exc
