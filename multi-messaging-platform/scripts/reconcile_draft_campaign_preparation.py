#!/usr/bin/env python3
"""Inventory and reconcile draft campaigns through the authoritative prepare service.

Read-only by default (--dry-run). With --apply, runs try_auto_prepare_campaign for
eligible drafts only (never RUNNING/COMPLETED/CANCELLED/FAILED).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.database import SessionLocal  # noqa: E402
from core_engine.models import Campaign, CampaignStatus  # noqa: E402
from core_engine.services.campaign_auto_prepare import (  # noqa: E402
    campaign_is_auto_prepare_candidate,
    try_auto_prepare_campaign,
)
from core_engine.services.campaign_preparation import evaluate_preparation_readiness  # noqa: E402


_PROTECTED = frozenset(
    {
        CampaignStatus.RUNNING.value,
        CampaignStatus.COMPLETED.value,
        CampaignStatus.FAILED.value,
        CampaignStatus.CANCELLED.value,
    }
)


def _classify(campaign: Campaign, blockers: list) -> str:
    if campaign.status in _PROTECTED:
        return campaign.status.upper()
    if campaign.status == CampaignStatus.PREPARED.value:
        return "PREPARED"
    if blockers:
        return "DRAFT_BLOCKED"
    return "DRAFT_VALID_READY_TO_PREPARE"


def _inventory(db: Session) -> list[dict]:
    rows: list[dict] = []
    campaigns = db.query(Campaign).order_by(Campaign.id.asc()).all()
    for campaign in campaigns:
        blockers = (
            evaluate_preparation_readiness(db, campaign)
            if campaign_is_auto_prepare_candidate(campaign)
            else []
        )
        classification = _classify(campaign, blockers)
        from core_engine.models import RenderedMessage

        final_count = (
            db.query(RenderedMessage)
            .filter(
                RenderedMessage.campaign_id == campaign.id,
                RenderedMessage.ready_for_queue.is_(True),
            )
            .count()
        )
        rows.append(
            {
                "campaign_id": campaign.id,
                "status": campaign.status,
                "use_gpt": bool(campaign.use_gpt),
                "include_products": bool(campaign.include_products),
                "classification": classification,
                "prepare_eligible": classification == "DRAFT_VALID_READY_TO_PREPARE",
                "preparation_blockers": [b.get("code") for b in blockers],
                "final_message_count": int(final_count),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run authoritative prepare for eligible drafts.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "campaign-forensics" / "GLOBAL_EXISTING_CAMPAIGN_RECONCILIATION.json",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        inventory = _inventory(db)
        for row in inventory:
            row["prepare_result"] = None
            row["blocker"] = None
            if not args.apply:
                continue
            if not row["prepare_eligible"]:
                row["blocker"] = row["preparation_blockers"][0] if row["preparation_blockers"] else row["classification"]
                continue
            result = try_auto_prepare_campaign(
                db,
                int(row["campaign_id"]),
                trigger="reconciliation",
                force=False,
            )
            if result.prepared:
                row["prepare_result"] = "PREPARED"
            elif result.blockers:
                row["prepare_result"] = "BLOCKED"
                row["blocker"] = result.blockers[0].get("code")
            elif result.error_code:
                row["prepare_result"] = "FAILED"
                row["blocker"] = result.error_code
            else:
                row["prepare_result"] = result.skip_reason or "SKIPPED"
            db.expire_all()
            row["final_message_count"] = next(
                item["final_message_count"]
                for item in _inventory(db)
                if item["campaign_id"] == row["campaign_id"]
            )

        summary = {
            "total_campaigns": len(inventory),
            "existing_valid_drafts": sum(
                1 for r in inventory if r["classification"] == "DRAFT_VALID_READY_TO_PREPARE"
            ),
            "existing_blocked_drafts": sum(
                1 for r in inventory if r["classification"] == "DRAFT_BLOCKED"
            ),
            "already_prepared": sum(
                1 for r in inventory if r["classification"] == "PREPARED"
            ),
            "existing_auto_prepared": sum(
                1 for r in inventory if r.get("prepare_result") == "PREPARED"
            ),
            "existing_prepare_failed": sum(
                1 for r in inventory if r.get("prepare_result") == "FAILED"
            ),
            "dry_run": not args.apply,
            "campaigns": inventory,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in summary.items() if k != "campaigns"}, ensure_ascii=False, indent=2))
        print(f"Wrote {args.output}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
