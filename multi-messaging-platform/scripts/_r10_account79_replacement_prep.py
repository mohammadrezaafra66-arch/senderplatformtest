"""R10 Account79 replacement PREP only — one campaign, one message, no send.

Creates Campaign ``R10-ACCOUNT79-REPLACEMENT-1MSG`` with Account79 + Contact2.
On any failure after first commit, deletes ONLY exact IDs created by this run.

Does NOT: send, enqueue Redis, retry Message338, mutate Campaign101, OTP, sessions.
"""
from __future__ import annotations

import asyncio
import json
import re
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
    Message,
    PlatformType,
    RenderedMessage,
    RenderStatus,
    SendStatus,
    StagedQueueItem,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
from core_engine.services.campaign_render import remaining_placeholders
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.redis_client import ensure_redis_client, reset_redis_client
from core_engine.services.rubika_preflight import evaluate_rubika_send_preflight
from workers.pool_health import has_active_worker_coverage
from workers.redis_keys import queue_key

NAME = "R10-ACCOUNT79-REPLACEMENT-1MSG"
TEMPLATE = "پیام کنترل‌شده R10 جایگزینی Account79 — بدون placeholder."
CONTACT_ID = 2
ACCOUNT_ID = 79
CAMPAIGN101_ID = 101
MESSAGE338_ID = 338
OUT_DIR = Path("/tmp/r10_send_prep")
OUT_PATH = OUT_DIR / "R10_ACCOUNT79_REPLACEMENT_PREP.json"
CREATED_IDS_PATH = OUT_DIR / "R10_ACCOUNT79_REPLACEMENT_CREATED_IDS.json"

SINGLE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def _unresolved(text_v: str | None) -> list[str]:
    names = list(remaining_placeholders(text_v or ""))
    for n in SINGLE.findall(text_v or ""):
        if n not in names:
            names.append(n)
    return names


def _mask_phone(p: str | None) -> dict:
    d = re.sub(r"\D", "", str(p or ""))
    return {
        "digit_len": len(d),
        "prefix": d[:2] if len(d) >= 2 else d,
        "suffix4": d[-4:] if len(d) >= 4 else d,
        "e164_ir": d.startswith("98") and len(d) == 12,
    }


def _fail(msg: str) -> None:
    raise RuntimeError(msg)


async def _q79() -> dict:
    reset_redis_client()
    r = await ensure_redis_client()
    return {
        "QUEUE79": int(await r.llen(queue_key("rubika", 79))),
        "QUEUE12": int(await r.llen(queue_key("rubika", 12))),
        "COV79": bool(
            await has_active_worker_coverage(r, platform="rubika", account_id=79)
        ),
    }


def _persist_created_ids(created: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CREATED_IDS_PATH.write_text(
        json.dumps(created, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def cleanup_exact_created_ids(created: dict) -> dict:
    """Delete ONLY exact IDs recorded for this replacement prep. No wildcards."""
    db = SessionLocal()
    deleted: dict = {"tables": {}}
    try:
        camp_id = created.get("campaign_id")
        if camp_id is None:
            return {"skipped": True, "reason": "no campaign_id recorded"}

        # Child → parent using exact IDs only
        staged_ids = list(created.get("staged_queue_item_ids") or [])
        rendered_ids = list(created.get("rendered_message_ids") or [])
        message_ids = list(created.get("message_ids") or [])
        recipient_ids = list(created.get("campaign_recipient_ids") or [])
        account_link_ids = list(created.get("campaign_account_ids") or [])

        # Also discover any prep rows for this campaign_id that were committed
        # but not yet recorded (prepare commit after partial id capture).
        if camp_id is not None:
            extra_staged = [
                int(r[0])
                for r in db.execute(
                    text(
                        "SELECT id FROM staged_queue_items WHERE campaign_id=:cid"
                    ),
                    {"cid": camp_id},
                ).fetchall()
            ]
            extra_rendered = [
                int(r[0])
                for r in db.execute(
                    text(
                        "SELECT id FROM rendered_messages WHERE campaign_id=:cid"
                    ),
                    {"cid": camp_id},
                ).fetchall()
            ]
            extra_messages = [
                int(r[0])
                for r in db.execute(
                    text("SELECT id FROM messages WHERE campaign_id=:cid"),
                    {"cid": camp_id},
                ).fetchall()
            ]
            extra_recipients = [
                int(r[0])
                for r in db.execute(
                    text(
                        "SELECT id FROM campaign_recipients WHERE campaign_id=:cid"
                    ),
                    {"cid": camp_id},
                ).fetchall()
            ]
            extra_links = [
                int(r[0])
                for r in db.execute(
                    text(
                        "SELECT id FROM campaign_accounts WHERE campaign_id=:cid"
                    ),
                    {"cid": camp_id},
                ).fetchall()
            ]
            staged_ids = sorted(set(staged_ids) | set(extra_staged))
            rendered_ids = sorted(set(rendered_ids) | set(extra_rendered))
            message_ids = sorted(set(message_ids) | set(extra_messages))
            recipient_ids = sorted(set(recipient_ids) | set(extra_recipients))
            account_link_ids = sorted(set(account_link_ids) | set(extra_links))

        # Safety: never allow cleanup of Campaign101 / known R10 send artifacts
        forbidden_campaigns = {CAMPAIGN101_ID}
        forbidden_messages = {337, MESSAGE338_ID}
        if camp_id in forbidden_campaigns:
            _fail("cleanup refused: campaign_id is protected Campaign101")
        if set(message_ids) & forbidden_messages:
            _fail("cleanup refused: protected message ids in created set")

        def _del(table: str, ids: list[int]) -> int:
            if not ids:
                deleted["tables"][table] = 0
                return 0
            # Exact-ID only
            n = db.execute(
                text(f"DELETE FROM {table} WHERE id = ANY(:ids)"),
                {"ids": ids},
            ).rowcount
            deleted["tables"][table] = int(n or 0)
            return int(n or 0)

        _del("staged_queue_items", staged_ids)
        _del("rendered_messages", rendered_ids)
        # Clear recipient FK to messages before message delete if needed
        if message_ids and recipient_ids:
            db.execute(
                text(
                    """
                    UPDATE campaign_recipients
                    SET final_message_id = NULL
                    WHERE id = ANY(:rids)
                      AND final_message_id = ANY(:mids)
                    """
                ),
                {"rids": recipient_ids, "mids": message_ids},
            )
        _del("messages", message_ids)
        _del("campaign_recipients", recipient_ids)
        _del("campaign_accounts", account_link_ids)
        _del("campaigns", [int(camp_id)])
        db.commit()
        deleted["ok"] = True
        deleted["campaign_id"] = camp_id
        deleted["staged_queue_item_ids"] = staged_ids
        deleted["rendered_message_ids"] = rendered_ids
        deleted["message_ids"] = message_ids
        deleted["campaign_recipient_ids"] = recipient_ids
        deleted["campaign_account_ids"] = account_link_ids
        return deleted
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def preflight_immutable(db) -> dict:
    c101 = db.query(Campaign).filter(Campaign.id == CAMPAIGN101_ID).first()
    if c101 is None:
        _fail("Campaign101 missing")
    if c101.status == CampaignStatus.RUNNING.value or str(c101.status) == "running":
        _fail(f"Campaign101 still running: {c101.status}")
    if str(c101.status) != "paused":
        # Accept paused as required stop state from post-send verification
        _fail(f"Campaign101 expected paused, got {c101.status}")

    n338 = int(
        db.execute(
            text("SELECT COUNT(*) FROM message_attempts WHERE message_id=:mid"),
            {"mid": MESSAGE338_ID},
        ).scalar()
    )
    if n338 != 1:
        _fail(f"Message338 attempt count expected 1 got {n338}")

    existing = db.query(Campaign).filter(Campaign.name == NAME).first()
    if existing is not None:
        _fail(f"replacement campaign name already exists id={existing.id}")

    c2 = db.query(Contact).filter(Contact.id == CONTACT_ID).first()
    if c2 is None or c2.id == 92:
        _fail("Contact2 missing/invalid")
    if c2.blacklisted or str(c2.consent_status).lower() != "allowed":
        _fail("Contact2 not eligible")
    pe = re.sub(r"\D", "", c2.phone_e164 or "")
    if not (pe.startswith("98") and len(pe) == 12):
        _fail(f"Contact2 phone not valid IR E.164 shape {_mask_phone(c2.phone_e164)}")

    return {
        "campaign101_status": c101.status,
        "message338_attempt_count": n338,
        "contact2_phone_masked": _mask_phone(c2.phone_e164),
        "REPLACEMENT_CONTACT_ID": CONTACT_ID,
        "PHONE_VALID_IR_E164": True,
        "CONSENT_ALLOWED": True,
        "BLACKLISTED": False,
    }


async def main_async() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "MESSAGE_SENT": False,
        "OTP_REQUESTED": False,
        "NEW_ACCOUNT_COUNT_CREATED": 0,
        "NEW_CONTACT_COUNT_CREATED": 0,
        "cleaned_up": False,
    }
    created: dict = {
        "campaign_id": None,
        "campaign_account_ids": [],
        "campaign_recipient_ids": [],
        "message_ids": [],
        "rendered_message_ids": [],
        "staged_queue_item_ids": [],
    }

    db = SessionLocal()
    try:
        gate = preflight_immutable(db)
        report.update(gate)

        a79 = db.query(Account).filter(Account.id == ACCOUNT_ID).first()
        if a79 is None:
            _fail("Account79 missing")
        t79 = await evaluate_rubika_send_preflight(
            db, account=a79, context="campaign", consume_circuit_probe=False
        )
        q = await _q79()
        if not t79.allowed or t79.code != "READY":
            _fail(f"Account79 not READY: {t79.code}")
        if not q["COV79"] or q["QUEUE79"] != 0:
            _fail(f"Account79 coverage/queue fail: {q}")
        report["ACCOUNT79_FINAL_ALLOWED"] = True
        report["ACCOUNT79_WORKER_COVERAGE"] = True
        report["QUEUE79"] = 0
        report["ACCOUNT79_CODE"] = t79.code

        # --- create draft campaign (first commit) ---
        camp = Campaign(
            name=NAME,
            channel="rubika",
            title=NAME,
            platform=PlatformType.RUBIKA,
            status=CampaignStatus.DRAFT.value,
            template_text=TEMPLATE,
            use_gpt=False,
            include_products=False,
        )
        db.add(camp)
        db.flush()
        link = CampaignAccount(
            campaign_id=camp.id,
            account_id=ACCOUNT_ID,
            priority=1,
            enabled=True,
        )
        db.add(link)
        recip = CampaignRecipient(
            campaign_id=camp.id,
            contact_id=CONTACT_ID,
            render_status=RenderStatus.PENDING,
            send_status=SendStatus.PENDING,
        )
        db.add(recip)
        db.flush()

        created["campaign_id"] = int(camp.id)
        created["campaign_account_ids"] = [int(link.id)]
        created["campaign_recipient_ids"] = [int(recip.id)]
        _persist_created_ids(created)
        db.commit()
        report["first_commit_ok"] = True
        report["R10_REPLACEMENT_CAMPAIGN_ID"] = created["campaign_id"]

        try:
            prep = prepare_campaign_messages(
                db,
                created["campaign_id"],
                PrepareMessagesRequest(limit=1, force_mock_output=False),
            )
            if prep.redis_queue_pushed:
                _fail("unexpected redis enqueue during prepare")

            # Record exact prep artifact IDs
            msgs = (
                db.query(Message)
                .filter(Message.campaign_id == created["campaign_id"])
                .order_by(Message.id.asc())
                .all()
            )
            renders = (
                db.query(RenderedMessage)
                .filter(RenderedMessage.campaign_id == created["campaign_id"])
                .order_by(RenderedMessage.id.asc())
                .all()
            )
            staged = (
                db.query(StagedQueueItem)
                .filter(StagedQueueItem.campaign_id == created["campaign_id"])
                .order_by(StagedQueueItem.id.asc())
                .all()
            )
            created["message_ids"] = [int(m.id) for m in msgs]
            created["rendered_message_ids"] = [int(r.id) for r in renders]
            created["staged_queue_item_ids"] = [int(s.id) for s in staged]
            _persist_created_ids(created)

            if len(msgs) != 1:
                _fail(f"expected exactly 1 message, got {len(msgs)}")
            m = msgs[0]
            if m.account_id != ACCOUNT_ID or m.contact_id != CONTACT_ID:
                _fail(
                    f"assignment wrong account={m.account_id} contact={m.contact_id}"
                )
            u = _unresolved(m.rendered_text)
            if u:
                _fail(f"unresolved placeholders: {u}")

            # Structural exactness
            n_links = (
                db.query(CampaignAccount)
                .filter(CampaignAccount.campaign_id == created["campaign_id"])
                .count()
            )
            n_recips = (
                db.query(CampaignRecipient)
                .filter(CampaignRecipient.campaign_id == created["campaign_id"])
                .count()
            )
            if n_links != 1 or n_recips != 1:
                _fail(f"expected 1 link/1 recip got links={n_links} recips={n_recips}")
            only_acct = (
                db.query(CampaignAccount)
                .filter(CampaignAccount.campaign_id == created["campaign_id"])
                .one()
            )
            if int(only_acct.account_id) != ACCOUNT_ID:
                _fail("campaign account is not 79")

            camp_pf = await evaluate_campaign_send_preflight(
                db, created["campaign_id"]
            )
            t79b = await evaluate_rubika_send_preflight(
                db,
                account=a79,
                campaign_id=created["campaign_id"],
                context="campaign",
                consume_circuit_probe=False,
            )
            q2 = await _q79()
            if not t79b.allowed or t79b.code != "READY":
                _fail(f"Account79 not allowed after prep: {t79b.code}")
            if not q2["COV79"] or q2["QUEUE79"] != 0:
                _fail(f"post-prep queue/coverage fail: {q2}")

            # Immutable R10 send artifacts still untouched
            c101 = db.query(Campaign).filter(Campaign.id == CAMPAIGN101_ID).first()
            if str(c101.status) != "paused":
                _fail(f"Campaign101 status changed: {c101.status}")
            n338 = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM message_attempts WHERE message_id=:mid"
                    ),
                    {"mid": MESSAGE338_ID},
                ).scalar()
            )
            if n338 != 1:
                _fail(f"Message338 attempt count changed: {n338}")

            report.update(
                {
                    "NEW_CAMPAIGN_COUNT_CREATED": 1,
                    "NEW_MESSAGE_COUNT_CREATED": 1,
                    "R10_REPLACEMENT_MESSAGE_ID": int(m.id),
                    "R10_REPLACEMENT_CONTACT_ID": CONTACT_ID,
                    "UNRESOLVED_PLACEHOLDER_COUNT": 0,
                    "ACCOUNT79_FINAL_ALLOWED": True,
                    "ACCOUNT79_WORKER_COVERAGE": True,
                    "QUEUE79": 0,
                    "campaign_preflight": {
                        "code": camp_pf.code,
                        "allowed_to_start": camp_pf.allowed_to_start,
                    },
                    "ACCOUNT79_CODE": t79b.code,
                    "rendered_text": m.rendered_text,
                    "created_ids": created,
                    "campaign101_status_after": c101.status,
                    "message338_attempt_count_after": n338,
                    "prepare": {
                        "ready_count": prep.ready_count,
                        "redis_queue_pushed": prep.redis_queue_pushed,
                    },
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            OUT_PATH.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return report
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["R10_REPLACEMENT_PREP_PASS"] = False
            # Explicit exact-ID cleanup (prepare may have committed)
            deleted = cleanup_exact_created_ids(created)
            report["cleaned_up"] = True
            report["cleanup"] = deleted
            report["NEW_CAMPAIGN_COUNT_CREATED"] = 0
            report["NEW_MESSAGE_COUNT_CREATED"] = 0
            OUT_PATH.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            raise
    finally:
        db.close()


def main() -> None:
    report = asyncio.run(main_async())
    report["R10_REPLACEMENT_PREP_PASS"] = True
    OUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
