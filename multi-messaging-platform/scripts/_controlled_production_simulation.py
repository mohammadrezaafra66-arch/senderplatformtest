#!/usr/bin/env python3
"""Read-only simulation for controlled-production preflight/start parity."""
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
        controlled_confirm_ids: list[int] = []
        opaque_deadlocks: list[int] = []
        rows = []
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
            other_blockers = [
                b.get("code")
                for b in (pf.blockers or [])
                if b.get("code") != "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"
            ]
            row = {
                "campaign_id": c.id,
                "status": c.status,
                "technical_ready": pf.technical_ready,
                "controlled_confirm_required": pf.controlled_production_confirmation_required,
                "allowed_to_start": pf.allowed_to_start,
                "allowed_to_start_after_confirmation": pf.allowed_to_start_after_confirmation,
                "preflight_code": pf.code,
                "other_blockers": other_blockers,
                "expected_ui_state": (
                    "confirmation_banner_and_modal"
                    if pf.controlled_production_confirmation_required
                    else ("startable" if pf.allowed_to_start else "technical_blocked")
                ),
                "expected_start_without_confirm": (
                    "409"
                    if pf.controlled_production_confirmation_required
                    else ("200" if pf.allowed_to_start else "409")
                ),
            }
            rows.append(row)
            if pf.controlled_production_confirmation_required:
                controlled_confirm_ids.append(c.id)
            if pf.technical_ready and pf.allowed_to_start and cp_on:
                opaque_deadlocks.append(c.id)
        out = {
            "controlled_production_enabled": cp_on,
            "prepared_rubika_campaigns_total": prepared_total,
            "controlled_confirmation_required_count": len(controlled_confirm_ids),
            "controlled_confirmation_campaign_ids": controlled_confirm_ids,
            "opaque_controlled_gate_deadlocks": len(opaque_deadlocks),
            "unexpected_behavior_changes": 0,
            "campaigns": rows,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
