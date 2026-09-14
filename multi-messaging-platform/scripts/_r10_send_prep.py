"""R10 controlled real-send PREPARATION only — create+prepare campaign; never start/send."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal
from core_engine.models import (
    Account,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    ConsentStatus,
    Message,
    PlatformType,
    RenderStatus,
    SendStatus,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.redis_client import ensure_redis_client, reset_redis_client
from core_engine.services.rubika_preflight import evaluate_rubika_send_preflight
from workers.pool_health import has_active_worker_coverage
from workers.redis_keys import queue_key

OUT = Path("/tmp/r10_send_prep")
CAMPAIGN_NAME = "R10-CONTROLLED-REAL-SEND-2MSG"
TEMPLATE = (
    "سلام {first_name} — پیام کنترل‌شده R10 (ارسال واقعی فقط پس از تایید اپراتور)."
)
ACCOUNT_IDS = [12, 79]
CONTACT_IDS = [2, 92]


async def _queues_cov() -> dict:
    reset_redis_client()
    r = await ensure_redis_client()
    return {
        "QUEUE12": int(await r.llen(queue_key("rubika", 12))),
        "QUEUE79": int(await r.llen(queue_key("rubika", 79))),
        "COV12": bool(
            await has_active_worker_coverage(r, platform="rubika", account_id=12)
        ),
        "COV79": bool(
            await has_active_worker_coverage(r, platform="rubika", account_id=79)
        ),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    q = asyncio.run(_queues_cov())
    if not q["COV12"] or not q["COV79"]:
        raise SystemExit(f"STOP: worker coverage invalid before campaign create: {q}")
    if q["QUEUE12"] != 0 or q["QUEUE79"] != 0:
        raise SystemExit(f"STOP: queues non-empty before campaign create: {q}")

    db = SessionLocal()
    report: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "MESSAGE_SENT": False,
        "OTP_REQUESTED": False,
        "queues_before_create": q,
    }
    try:
        # Idempotent: reuse existing R10 prep campaign if present and still draft/prepared
        existing = (
            db.query(Campaign)
            .filter(Campaign.name == CAMPAIGN_NAME)
            .order_by(Campaign.id.desc())
            .first()
        )
        if existing is not None:
            raise SystemExit(
                f"STOP: campaign name already exists id={existing.id} "
                f"status={existing.status}; refuse duplicate R10 campaign"
            )

        for cid in CONTACT_IDS:
            c = db.query(Contact).filter(Contact.id == cid).first()
            if c is None:
                raise SystemExit(f"STOP: contact {cid} missing")
            if c.blacklisted:
                raise SystemExit(f"STOP: contact {cid} blacklisted")
            if c.consent_status == ConsentStatus.BLOCKED.value:
                raise SystemExit(f"STOP: contact {cid} consent blocked")

        for aid in ACCOUNT_IDS:
            a = db.query(Account).filter(Account.id == aid).first()
            if a is None:
                raise SystemExit(f"STOP: account {aid} missing")

        campaign = Campaign(
            name=CAMPAIGN_NAME,
            channel="rubika",
            title=CAMPAIGN_NAME,
            platform=PlatformType.RUBIKA,
            status=CampaignStatus.DRAFT.value,
            template_text=TEMPLATE,
            use_gpt=False,
            include_products=False,
        )
        db.add(campaign)
        db.flush()

        for priority, aid in enumerate(ACCOUNT_IDS, start=1):
            db.add(
                CampaignAccount(
                    campaign_id=campaign.id,
                    account_id=aid,
                    priority=priority,
                    enabled=True,
                )
            )

        for cid in CONTACT_IDS:
            db.add(
                CampaignRecipient(
                    campaign_id=campaign.id,
                    contact_id=cid,
                    render_status=RenderStatus.PENDING,
                    send_status=SendStatus.PENDING,
                )
            )
        db.commit()
        db.refresh(campaign)
        report["R10_CAMPAIGN_ID"] = campaign.id
        report["NEW_CAMPAIGN_COUNT_CREATED"] = 1
        report["NEW_ACCOUNT_COUNT_CREATED"] = 0
        report["NEW_CONTACT_COUNT_CREATED"] = 0

        # Prepare (DB staging only — does not push Redis / does not send)
        prep = prepare_campaign_messages(
            db,
            campaign.id,
            PrepareMessagesRequest(limit=2, force_mock_output=False),
        )
        report["prepare"] = prep.model_dump() if hasattr(prep, "model_dump") else str(prep)

        msgs = (
            db.query(Message)
            .filter(Message.campaign_id == campaign.id)
            .order_by(Message.id.asc())
            .all()
        )
        if len(msgs) != 2:
            raise SystemExit(f"STOP: expected 2 messages, got {len(msgs)}")

        by_acct: dict[int, int] = {}
        for m in msgs:
            by_acct[m.account_id] = by_acct.get(m.account_id, 0) + 1
        report["assignment"] = {
            "messages": [
                {
                    "id": m.id,
                    "account_id": m.account_id,
                    "contact_id": m.contact_id,
                }
                for m in msgs
            ],
            "by_account": by_acct,
        }
        if by_acct.get(12) != 1 or by_acct.get(79) != 1:
            raise SystemExit(
                f"STOP: assignment not 1/1 (got {by_acct}); refuse send prep"
            )

        report["R10_RECIPIENT_COUNT"] = int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM campaign_recipients WHERE campaign_id=:cid"
                ),
                {"cid": campaign.id},
            ).scalar()
        )
        report["R10_MESSAGE_COUNT"] = len(msgs)
        report["ACCOUNT12_ASSIGNED_MESSAGE_COUNT"] = by_acct.get(12, 0)
        report["ACCOUNT79_ASSIGNED_MESSAGE_COUNT"] = by_acct.get(79, 0)
        report["NEW_MESSAGE_COUNT_CREATED"] = 2

        # Authoritative preflight (campaign + transport) — read-only planning
        async def _pf() -> dict:
            reset_redis_client()
            camp_pf = await evaluate_campaign_send_preflight(db, campaign.id)
            a12 = db.query(Account).filter(Account.id == 12).first()
            a79 = db.query(Account).filter(Account.id == 79).first()
            t12 = await evaluate_rubika_send_preflight(
                db,
                account=a12,
                campaign_id=campaign.id,
                context="campaign",
                consume_circuit_probe=False,
            )
            t79 = await evaluate_rubika_send_preflight(
                db,
                account=a79,
                campaign_id=campaign.id,
                context="campaign",
                consume_circuit_probe=False,
            )

            def _ser(r) -> dict:
                return {
                    "allowed": bool(r.allowed),
                    "final_allowed": bool(r.allowed),
                    "code": r.code,
                    "message": r.message,
                    "account_id": r.account_id,
                    "details": dict(r.details or {}),
                }

            q2 = await _queues_cov()
            return {
                "campaign": camp_pf.to_dict(),
                "transport_12": _ser(t12),
                "transport_79": _ser(t79),
                "queues_after_prepare": q2,
            }

        pf = asyncio.run(_pf())
        report["preflight"] = pf
        q_after = pf["queues_after_prepare"]
        if q_after["QUEUE12"] != 0 or q_after["QUEUE79"] != 0:
            raise SystemExit(f"STOP: queues non-zero after prepare: {q_after}")

        t12 = pf["transport_12"]
        t79 = pf["transport_79"]
        report["ACCOUNT12_FINAL_ALLOWED"] = bool(t12.get("final_allowed"))
        report["ACCOUNT79_FINAL_ALLOWED"] = bool(t79.get("final_allowed"))
        report["ACCOUNT12_PREFLIGHT_CODE"] = t12.get("code")
        report["ACCOUNT79_PREFLIGHT_CODE"] = t79.get("code")
        report["ACCOUNT12_GATES"] = t12.get("details")
        report["ACCOUNT79_GATES"] = t79.get("details")
        report["CAMPAIGN_ALLOWED_TO_START"] = bool(
            pf["campaign"].get("allowed_to_start")
        )
        report["ACCOUNT12_WORKER_COVERAGE"] = q_after["COV12"]
        report["ACCOUNT79_WORKER_COVERAGE"] = q_after["COV79"]
        report["QUEUE12"] = q_after["QUEUE12"]
        report["QUEUE79"] = q_after["QUEUE79"]
        report["finished_at"] = datetime.now(timezone.utc).isoformat()

        (OUT / "R10_SEND_PREP.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str)[:8000])
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
