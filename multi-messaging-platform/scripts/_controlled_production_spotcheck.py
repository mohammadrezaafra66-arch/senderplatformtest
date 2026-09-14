#!/usr/bin/env python3
"""Live preflight spot-check for campaigns 125/126/127."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.database import SessionLocal
from core_engine.models import Campaign
from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
from core_engine.services.campaign_production_guards import controlled_production_enabled


async def main() -> int:
    db = SessionLocal()
    try:
        out: dict = {"cp_enabled": controlled_production_enabled(), "campaigns": {}}
        for cid in (125, 126, 127):
            c = db.query(Campaign).filter(Campaign.id == cid).first()
            if not c:
                out["campaigns"][str(cid)] = {"exists": False}
                continue
            pf = await evaluate_campaign_send_preflight(db, cid)
            out["campaigns"][str(cid)] = {
                "exists": True,
                "status": c.status,
                "technical_ready": pf.technical_ready,
                "controlled_confirm_required": pf.controlled_production_confirmation_required,
                "allowed_to_start": pf.allowed_to_start,
                "allowed_after_confirm": pf.allowed_to_start_after_confirmation,
                "code": pf.code,
                "controlled_production_label": pf.controlled_production_label,
                "other_blockers": [
                    b.get("code")
                    for b in pf.blockers
                    if b.get("code") != "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"
                ],
            }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
