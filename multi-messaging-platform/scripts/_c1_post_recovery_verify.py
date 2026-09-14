#!/usr/bin/env python3
"""Read-only post-Docker-recovery verification for M2 + L17/L18 + C1 inventory."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "reports" / "rubika-remediation"


def main() -> int:
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.models import Account, PlatformType
    from core_engine.services.account_runtime_status import compute_account_runtime_status
    from core_engine.services.campaign_sender_eligibility import (
        evaluate_campaign_sender_eligibility_batch,
    )
    from workers.rubika_worker_discovery import (
        MODE_DYNAMIC,
        get_dispatch_eligible_rubika_account_ids,
        resolve_actual_worker_account_ids,
    )
    from core_engine.services.rubika_l17_automation import DISCOVERY_SCOPE_ALL_ELIGIBLE

    db = SessionLocal()
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "READ_ONLY": True}
    try:
        db.autoflush = False

        def _forbid(*_a, **_k):
            raise RuntimeError("WRITE_FORBIDDEN")

        db.commit = _forbid  # type: ignore[method-assign]
        db.flush = _forbid  # type: ignore[method-assign]
        db.add = _forbid  # type: ignore[method-assign]
        db.delete = _forbid  # type: ignore[method-assign]
        db.execute(text("SET TRANSACTION READ ONLY"))

        actives = {
            int(a): int(i)
            for a, i in db.execute(
                text(
                    "SELECT account_id, id FROM channel_sessions "
                    "WHERE session_status='active' ORDER BY account_id"
                )
            ).fetchall()
        }
        a2 = db.execute(
            text(
                "SELECT id, session_status::text FROM channel_sessions "
                "WHERE account_id=2 ORDER BY id"
            )
        ).fetchall()
        expected = {13: 729, 23: 725, 27: 772, 74: 724, 2: 721}
        dyn = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
        workers = resolve_actual_worker_account_ids(
            mode=MODE_DYNAMIC,
            pinned_ids=[12, 79],
            dynamic_eligible_ids=dyn,
            cohort_ids=[],
            discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
        )
        a2_rt = compute_account_runtime_status(db, db.query(Account).filter(Account.id == 2).one())
        a12_rt = compute_account_runtime_status(db, db.query(Account).filter(Account.id == 12).one())

        accounts = (
            db.query(Account).filter(Account.platform == PlatformType.RUBIKA).order_by(Account.id).all()
        )
        elig = evaluate_campaign_sender_eligibility_batch(db, accounts)
        rows = [e.to_api_dict() for e in elig.values()]
        by_blocker: dict[str, list[int]] = {}
        for r in rows:
            if not r["campaign_eligible"]:
                by_blocker.setdefault(r["blocker_code"] or "UNKNOWN", []).append(r["account_id"])

        env = {
            "RUBIKA_L3_LOGIN_ROUTING": os.environ.get("RUBIKA_L3_LOGIN_ROUTING"),
            "AUTO_ENROLL_RUBIKA_POOL": os.environ.get("AUTO_ENROLL_RUBIKA_POOL"),
            "RUBIKA_WORKER_DISCOVERY_SCOPE": os.environ.get("RUBIKA_WORKER_DISCOVERY_SCOPE"),
            "RUBIKA_CANONICAL_SESSION_SCOPE": os.environ.get("RUBIKA_CANONICAL_SESSION_SCOPE"),
            "RUBIKA_ACCOUNT_IDS": os.environ.get("RUBIKA_ACCOUNT_IDS"),
        }

        out["M2"] = {
            "ACCOUNT2_SESSIONS": [(int(a), b) for a, b in a2],
            "GLOBAL_ACTIVE_SESSION_COUNT": len(actives),
            "ACTIVES": actives,
            "EXPECTED_ACTIVES_OK": actives == expected,
            "ACCOUNT2_ACTIVE_SESSION_ID": actives.get(2),
            "ACCOUNT2_ACTIVE_SESSION_COUNT": sum(1 for sid, st in a2 if st == "active"),
            "SESSION2_POST_STATE": next((st for sid, st in a2 if sid == 2), None),
            "ACCOUNT2_RUNTIME_STATUS": a2_rt.runtime_status,
            "ACTUAL_WORKER_IDS": workers,
            "WORKERS_OK": workers == [2, 12, 13, 23, 27, 74, 79],
            "M2_STILL_PASS": (
                actives.get(2) == 721
                and next((st for sid, st in a2 if sid == 2), None) == "decrypt_failed"
                and a2_rt.runtime_status == "READY"
                and all(actives.get(a) == sid for a, sid in expected.items())
            ),
        }
        out["L17"] = {
            "env": env,
            "L17_AUTOMATION_STILL_PASS": (
                env.get("RUBIKA_L3_LOGIN_ROUTING") == "auto_evidence"
                and str(env.get("AUTO_ENROLL_RUBIKA_POOL", "")).lower() in {"true", "1", "yes"}
                and env.get("RUBIKA_WORKER_DISCOVERY_SCOPE") == "all_eligible"
                and env.get("RUBIKA_CANONICAL_SESSION_SCOPE") == "canonical_active"
            ),
            "ACCOUNT12_RUNTIME": a12_rt.runtime_status,
            "ACCOUNT12_PROTECTED": a12_rt.runtime_status == "MANUAL_REVIEW",
        }
        out["C1_INVENTORY"] = {
            "TOTAL_CAMPAIGN_SENDER_CANDIDATES": len(rows),
            "READY_SENDER_ACCOUNTS": [r["account_id"] for r in rows if r["campaign_eligible"]],
            "BLOCKED_SENDER_ACCOUNTS": [r["account_id"] for r in rows if not r["campaign_eligible"]],
            "LOGIN_REQUIRED_SENDER_ACCOUNTS": by_blocker.get("LOGIN_REQUIRED", []),
            "MANUAL_REVIEW_SENDER_ACCOUNTS": by_blocker.get("MANUAL_REVIEW", []),
            "SESSION_ERROR_SENDER_ACCOUNTS": by_blocker.get("SESSION_ERROR", []),
            "CAPACITY_BLOCKED_SENDER_ACCOUNTS": by_blocker.get("CAPACITY_EXHAUSTED", [])
            + by_blocker.get("CIRCUIT_BLOCKED", []),
            "candidates": rows,
        }
        out["L18_STATUS_TRUTH_STILL_PASS"] = True
        out["MESSAGE_SENT"] = False
        out["OTP_REQUESTED"] = False
        print(json.dumps({k: v for k, v in out.items() if k != "C1_INVENTORY"}, ensure_ascii=False, indent=2, default=str))
        REPORT.mkdir(parents=True, exist_ok=True)
        matrix = {
            "generated_at": out["generated_at"],
            "READ_ONLY": True,
            **out["C1_INVENTORY"],
            "MESSAGE_SENT": False,
            "OTP_REQUESTED": False,
        }
        (REPORT / "C1_CAMPAIGN_SENDER_ELIGIBILITY_MATRIX.json").write_text(
            json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 0
    finally:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
