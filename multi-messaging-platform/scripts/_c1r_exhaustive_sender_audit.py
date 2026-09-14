#!/usr/bin/env python3
"""C1R exhaustive campaign sender API audit (read-only)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "reports" / "rubika-remediation" / "C1R_EXHAUSTIVE_SENDER_AUDIT.json"

LABEL_BY_STATUS = {
    "READY": "آماده ارسال",
    "MANUAL_REVIEW": "نیازمند بررسی",
    "LOGIN_REQUIRED": "نیاز به ورود",
    "SESSION_ERROR": "خطای سشن",
    "CONNECTION_ERROR": "خطای اتصال",
    "AUTHENTICATED_NO_WORKER": "متصل، Worker آماده نیست",
}


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
    base = os.environ.get("MMP_API_BASE", "http://127.0.0.1:8001")
    user = os.environ.get("MMP_AUDIT_USER", "admin")
    password = os.environ.get("MMP_AUDIT_PASSWORD", "admin123")
    campaign_id = int(os.environ.get("MMP_AUDIT_CAMPAIGN_ID", "103"))

    token = _login(base, user, password)
    accounts = _get(base, "/accounts?platform=rubika", token)
    preflight = _get(base, f"/campaigns/{campaign_id}/preflight", token)

    acct_items = {int(a["id"]): a for a in accounts.get("items", [])}
    rubika_active = [
        a for a in accounts.get("items", [])
        if a.get("platform") == "rubika" and a.get("status") == "active"
    ]

    pf_by_id = {int(r["account_id"]): r for r in preflight.get("accounts", [])}
    rows = []
    groups: Counter[str] = Counter()

    for a in rubika_active:
        aid = int(a["id"])
        pf = pf_by_id.get(aid, {})
        rs = a.get("runtime_status")
        expected = a.get("campaign_status_label") or a.get("runtime_status_label") or LABEL_BY_STATUS.get(rs or "", rs)
        eligible = bool(a.get("campaign_eligible"))
        rows.append(
            {
                "ACCOUNT_ID": aid,
                "PLATFORM": a.get("platform"),
                "DISPLAY_IDENTITY": a.get("display_identity"),
                "ACCOUNT_ENABLED": a.get("account_enabled"),
                "L18_RUNTIME_STATUS": rs,
                "L18_RUNTIME_LABEL": a.get("runtime_status_label"),
                "CAMPAIGN_RUNTIME_STATUS": pf.get("readiness") or pf.get("runtime_status"),
                "CAMPAIGN_ELIGIBLE": eligible,
                "EXPECTED_CAMPAIGN_LABEL": expected,
                "EXPECTED_SELECTABLE": eligible,
                "BLOCKER_CODE": a.get("campaign_blocker_code"),
                "BLOCKER_LABEL": a.get("campaign_blocker_label"),
                "API_HAS_RUNTIME_STATUS": rs is not None,
                "API_HAS_CAMPAIGN_ELIGIBLE": a.get("campaign_eligible") is not None,
                "API_HAS_BLOCKER": bool(a.get("campaign_blocker_code") or a.get("campaign_blocker_label")),
                "DISPLAY_IDENTITY_VALID": bool(a.get("display_identity") and not str(a.get("display_identity")).strip().isdigit()),
            }
        )
        groups[rs or "UNKNOWN"] += 1

    out = {
        "TOTAL_CAMPAIGN_SENDER_CANDIDATES": len(rubika_active),
        "CAMPAIGN_ACCOUNTS_CHECKED_COUNT": len(rows),
        "ALL_CAMPAIGN_ACCOUNTS_CHECKED": len(rows) == len(rubika_active),
        "CLASSIFIED_CAMPAIGN_SENDER_CANDIDATES": sum(groups.values()),
        "UNCLASSIFIED_CAMPAIGN_SENDER_CANDIDATES": groups.get("UNKNOWN", 0),
        "STATUS_GROUPS": dict(groups),
        "LIVE_RESPONSE_HAS_RUNTIME_STATUS": all(r["API_HAS_RUNTIME_STATUS"] for r in rows),
        "LIVE_RESPONSE_HAS_CAMPAIGN_ELIGIBLE": all(r["API_HAS_CAMPAIGN_ELIGIBLE"] for r in rows),
        "rows": rows,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in out if k != "rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        raise SystemExit(1) from exc
