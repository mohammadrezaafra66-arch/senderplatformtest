#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.database import SessionLocal
from core_engine.models import Campaign, CampaignRecipient, RenderStatus, RenderedMessage
from core_engine.services.campaign_preparation import evaluate_preparation_readiness

db = SessionLocal()
for cid in (126, 103):
    c = db.query(Campaign).filter(Campaign.id == cid).first()
    if not c:
        print(f"Campaign {cid}: NOT FOUND")
        continue
    prep = (
        db.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == cid, RenderedMessage.ready_for_queue.is_(True))
        .count()
    )
    pending = (
        db.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == cid,
            CampaignRecipient.render_status == RenderStatus.PENDING,
        )
        .count()
    )
    blockers = [b.get("code") for b in evaluate_preparation_readiness(db, c)]
    print(
        f"Campaign {cid}: status={c.status} prepared_msgs={prep} "
        f"render_pending={pending} include_products={c.include_products} "
        f"use_gpt={c.use_gpt} blockers={blockers}"
    )
db.close()
