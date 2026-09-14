#!/usr/bin/env python3
"""Account79 stability observer — 3 consecutive READY cycles."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone

ACCOUNT_ID = 79
CYCLES = 3
INTERVAL_SEC = 5


async def snapshot() -> dict:
    from core_engine.database import SessionLocal
    from core_engine.models import Account
    from core_engine.services.account_runtime_status import compute_all_account_runtime_statuses
    from core_engine.services.campaign_sender_eligibility import evaluate_campaign_sender_eligibility

    db = SessionLocal()
    try:
        acct = db.query(Account).filter(Account.id == ACCOUNT_ID).one()
        runtime = compute_all_account_runtime_statuses(db, [acct])[0]
        elig = evaluate_campaign_sender_eligibility(db, acct, runtime=runtime)
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "runtime_status": runtime.runtime_status,
            "campaign_eligible": elig.campaign_eligible,
            "worker_ready": elig.worker_ready,
            "dispatch_ready": elig.dispatch_ready,
            "worker_covered": runtime.worker_covered,
            "reason_code": runtime.reason_code,
        }
    finally:
        db.close()


async def main() -> int:
    rows = []
    for i in range(CYCLES):
        row = await snapshot()
        row["cycle"] = i + 1
        rows.append(row)
        ok = (
            row["runtime_status"] == "READY"
            and row["campaign_eligible"] is True
            and row["worker_ready"] is True
            and row["dispatch_ready"] is True
        )
        if not ok:
            out = {
                "ACCOUNT79_READY_STABILITY_CYCLES": i,
                "ACCOUNT79_READY_STABLE": False,
                "cycles": rows,
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1
        if i < CYCLES - 1:
            await asyncio.sleep(INTERVAL_SEC)

    out = {
        "ACCOUNT79_READY_STABILITY_CYCLES": CYCLES,
        "ACCOUNT79_READY_STABLE": True,
        "cycles": rows,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
