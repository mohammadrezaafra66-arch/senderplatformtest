#!/usr/bin/env python3
"""C1 read-only production simulation of campaign sender eligibility. No mutations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "reports" / "rubika-remediation"
OUT = REPORT / "C1_CAMPAIGN_SENDER_ELIGIBILITY_MATRIX.json"
MISMATCH = REPORT / "C1_CAMPAIGN_UI_MISMATCH_MATRIX.json"


def main() -> int:
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.models import Account, PlatformType
    from core_engine.services.campaign_sender_eligibility import (
        evaluate_campaign_sender_eligibility_batch,
    )

    db = SessionLocal()
    try:
        db.autoflush = False

        def _forbid(*_a, **_k):
            raise RuntimeError("C1_READONLY_WRITE_FORBIDDEN")

        db.commit = _forbid  # type: ignore[method-assign]
        db.flush = _forbid  # type: ignore[method-assign]
        db.add = _forbid  # type: ignore[method-assign]
        db.delete = _forbid  # type: ignore[method-assign]
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.execute(text("SET TRANSACTION READ ONLY"))

        accounts = (
            db.query(Account)
            .filter(Account.platform == PlatformType.RUBIKA)
            .order_by(Account.id.asc())
            .all()
        )
        elig = evaluate_campaign_sender_eligibility_batch(db, accounts)
        rows = []
        mismatches = []
        for a in accounts:
            e = elig[int(a.id)]
            # Legacy misleading UI label was lifecycle-active → "فعال"
            current_ui_lie = "فعال · rubika" if a.status.value == "active" else a.status.value
            expected = e.runtime_status_label
            mismatch = current_ui_lie != f"rubika · {expected}" and e.enabled
            row = {
                "account_id": e.account_id,
                "platform": e.platform,
                "display_identity": e.display_identity,
                "runtime_status": e.runtime_status,
                "campaign_eligible": e.campaign_eligible,
                "blocker_code": e.blocker_code,
                "current_ui_label": current_ui_lie,
                "expected_ui_label": expected,
                "mismatch": bool(e.enabled and e.runtime_status != "READY")
                or (e.enabled and not e.campaign_eligible),
            }
            rows.append(row)
            if row["mismatch"]:
                mismatches.append(row)

        ready = [r for r in rows if r["campaign_eligible"]]
        blocked = [r for r in rows if not r["campaign_eligible"]]
        by_blocker: dict[str, list[int]] = {}
        for r in blocked:
            by_blocker.setdefault(r["blocker_code"] or "UNKNOWN", []).append(r["account_id"])

        matrix = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "READ_ONLY": True,
            "TOTAL_CAMPAIGN_SENDER_CANDIDATES": len(rows),
            "READY_SENDER_ACCOUNTS": [r["account_id"] for r in ready],
            "BLOCKED_SENDER_ACCOUNTS": [r["account_id"] for r in blocked],
            "LOGIN_REQUIRED_SENDER_ACCOUNTS": by_blocker.get("LOGIN_REQUIRED", []),
            "MANUAL_REVIEW_SENDER_ACCOUNTS": by_blocker.get("MANUAL_REVIEW", []),
            "SESSION_ERROR_SENDER_ACCOUNTS": by_blocker.get("SESSION_ERROR", []),
            "CAPACITY_BLOCKED_SENDER_ACCOUNTS": by_blocker.get("CAPACITY_EXHAUSTED", [])
            + by_blocker.get("CIRCUIT_BLOCKED", []),
            "candidates": rows,
            "MESSAGE_SENT": False,
            "OTP_REQUESTED": False,
        }
        mismatch_doc = {
            "generated_at": matrix["generated_at"],
            "MISMATCH_COUNT": len(mismatches),
            "mismatches": mismatches,
            "note": "Pre-C1 UI used lifecycle فعال; expected labels are L18/campaign eligibility.",
        }
        REPORT.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        MISMATCH.write_text(
            json.dumps(mismatch_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({k: matrix[k] for k in matrix if k != "candidates"}, ensure_ascii=False, indent=2))
        return 0
    finally:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
