#!/usr/bin/env python3
"""E2E campaign forensics — read-only production impact simulation."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "campaign-forensics"


def main() -> int:
    from core_engine.database import SessionLocal
    from core_engine.models import PlatformType
    from core_engine.services.campaign_readiness_contract import audit_campaign_sender_candidates

    db = SessionLocal()
    try:
        audit = audit_campaign_sender_candidates(db, platform=PlatformType.RUBIKA)
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "READ_ONLY": True,
            "platform": "rubika",
            "TOTAL_CAMPAIGN_SENDER_CANDIDATES": audit.total_candidates,
            "CAMPAIGN_ELIGIBLE_COUNT": audit.campaign_eligible_count,
            "BLOCKED_COUNT": audit.blocked_count,
            "STATUS_GROUPS": audit.status_groups,
            "ALL_CAMPAIGN_SENDERS_CHECKED": True,
            "rows": audit.to_dict()["rows"],
        }
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / "E2E_PRODUCTION_IMPACT_SIMULATION.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: out[k] for k in out if k != "rows"}, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
