#!/usr/bin/env python3
"""Read-only inventory: prepared Rubika campaigns blocked only by controlled-production gate."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.database import SessionLocal
from core_engine.models import Campaign, CampaignStatus, PlatformType, RenderedMessage
from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
from core_engine.services.campaign_production_guards import controlled_production_enabled


async def main() -> int:
    db = SessionLocal()
    try:
        cp_on = controlled_production_enabled()
        campaigns = (
            db.query(Campaign)
            .filter(Campaign.platform == PlatformType.RUBIKA)
            .order_by(Campaign.id.asc())
            .all()
        )
        prepared_total = 0
        ready_but_gate = []
        for c in campaigns:
            prepared_count = (
                db.query(RenderedMessage)
                .filter(
                    RenderedMessage.campaign_id == c.id,
                    RenderedMessage.ready_for_queue.is_(True),
                )
                .count()
            )
            is_prepared = c.status == CampaignStatus.PREPARED.value or prepared_count > 0
            if not is_prepared:
                continue
            prepared_total += 1
            pf = await evaluate_campaign_send_preflight(db, c.id)
            if cp_on and pf.allowed_to_start:
                ready_but_gate.append(
                    {
                        "campaign_id": c.id,
                        "status": c.status,
                        "preflight_code": pf.code,
                        "prepared_messages": pf.prepared_messages,
                    }
                )
        out = {
            "controlled_production_enabled": cp_on,
            "prepared_rubika_campaigns_total": prepared_total,
            "ready_but_controlled_gate_blocked": len(ready_but_gate),
            "affected_campaign_ids": [row["campaign_id"] for row in ready_but_gate],
            "details": ready_but_gate,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
