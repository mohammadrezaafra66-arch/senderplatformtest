"""Bridge DB-staged messages into real worker Redis queues.

This module is intentionally separate from:
- core_engine.services.phase4_staging (read-only inspection)
- core_engine.services.queue_manager (dry-run/shadow queue manager)

This is the first place where we push to the real worker delivery queues.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import (
    Campaign,
    CampaignStatus,
    Message,
    PlatformType,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.consent_service import get_consent_block_reason
from core_engine.services.redis_client import get_redis_client
from workers.redis_keys import queue_key

logger = logging.getLogger(__name__)

def _is_baileys_mode() -> bool:
    return get_settings().WHATSAPP_DELIVERY_MODE.strip().lower() == "baileys"



async def push_staged_items_to_worker_queue(
    db: Session,
    batch_size: int = 100,
) -> dict[str, int]:
    settings = get_settings()
    if not settings.REAL_QUEUE_PUSH_ENABLED:
        return {
            "pushed": 0,
            "skipped_consent": 0,
            "skipped_no_account": 0,
            "skipped_invalid": 0,
            "failed": 0,
        }

    pushed = 0
    skipped_consent = 0
    skipped_no_account = 0
    skipped_invalid = 0
    failed = 0

    # (a) Claim items using row-level locking and SKIP LOCKED.
    # Items staged outside the normal prepare path have no rendered_message_id.
    # They carry no verifiable rendered text, so they are never claimed — their
    # status is left untouched rather than being silently sent or failed.
    incomplete_items = (
        db.query(StagedQueueItem)
        .join(Campaign, StagedQueueItem.campaign_id == Campaign.id)
        .filter(
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
            Campaign.status == CampaignStatus.RUNNING.value,
            StagedQueueItem.rendered_message_id.is_(None),
        )
        .count()
    )
    if incomplete_items:
        logger.warning(
            "queue bridge is ignoring %s staged item(s) with no rendered_message_id; "
            "re-prepare the campaign to rebuild them",
            incomplete_items,
        )

    claimed_items = (
        db.query(StagedQueueItem)
        .join(Campaign, StagedQueueItem.campaign_id == Campaign.id)
        .filter(
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
            Campaign.status == CampaignStatus.RUNNING.value,
            StagedQueueItem.rendered_message_id.isnot(None),
        )
        .order_by(StagedQueueItem.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .all()
    )

    if not claimed_items:
        return {
            "pushed": 0,
            "skipped_consent": 0,
            "skipped_no_account": 0,
            "skipped_invalid": 0,
            "failed": 0,
        }

    for item in claimed_items:
        item.status = StagedQueueItemStatus.PUSHING.value

    # Commit early to release locks quickly.
    db.commit()

    redis = get_redis_client()
    message_ids = {
        int(item.queue_payload["message_id"])
        for item in claimed_items
        if item.queue_payload
        and str(item.queue_payload.get("message_id", "")).isdigit()
    }
    messages_by_id = (
        {
            message.id: message
            for message in db.query(Message).filter(Message.id.in_(message_ids)).all()
        }
        if message_ids
        else {}
    )

    # (b) Process claimed items after releasing locks.
    for item in claimed_items:
        try:
            # Never fall back to placeholder or stale text: an item without a
            # usable rendered payload is parked as SKIPPED with an explicit
            # reason so it stays visible instead of going out wrong.
            payload_text = str((item.queue_payload or {}).get("final_text") or "").strip()
            if not item.queue_payload or not payload_text:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = "invalid_payload:missing_rendered_text"
                skipped_invalid += 1
                logger.warning(
                    "queue bridge skipped staged_item=%s: no rendered text in payload",
                    item.id,
                )
                db.commit()
                continue

            block_reason = get_consent_block_reason(
                db,
                contact_id=item.contact_id,
                platform=item.channel,
            )
            if block_reason is not None:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = f"consent_blocked:{block_reason}"
                skipped_consent += 1
                db.commit()
                continue

            try:
                platform_enum = PlatformType(str(item.channel).strip().lower())
            except Exception:
                platform_enum = None

            if platform_enum is None:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = f"invalid_platform:{item.channel}"
                failed += 1
                db.commit()
                continue

            payload: dict[str, Any] = dict(item.queue_payload or {})
            try:
                message_id = int(payload["message_id"])
                payload_account_id = int(payload["account_id"])
            except (KeyError, TypeError, ValueError):
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = "invalid_payload:missing_sender_assignment"
                skipped_invalid += 1
                db.commit()
                continue

            message = messages_by_id.get(message_id)
            if message is None:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = "invalid_payload:message_not_found"
                skipped_invalid += 1
                db.commit()
                continue
            if (
                message.campaign_id != item.campaign_id
                or message.contact_id != item.contact_id
                or message.account_id != payload_account_id
            ):
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = "invalid_payload:sender_assignment_mismatch"
                skipped_invalid += 1
                db.commit()
                continue

            if platform_enum == PlatformType.WHATSAPP and _is_baileys_mode():
                from core_engine.services.baileys_queue import enqueue_baileys_from_worker_payload

                await enqueue_baileys_from_worker_payload(db, payload, route="campaign")
            else:
                key = queue_key(platform_enum.value, message.account_id)
                raw_payload = json.dumps(payload, ensure_ascii=False)
                await redis.rpush(key, raw_payload)


            item.status = StagedQueueItemStatus.QUEUED.value
            item.skip_reason = None
            pushed += 1
            db.commit()
        except Exception as exc:
            logger.exception("queue bridge failed for staged_item=%s", getattr(item, "id", None))
            item.status = StagedQueueItemStatus.READY.value
            item.skip_reason = f"bridge_failed:{exc.__class__.__name__}"
            failed += 1
            db.commit()

    return {
        "pushed": pushed,
        "skipped_consent": skipped_consent,
        "skipped_no_account": skipped_no_account,
        "skipped_invalid": skipped_invalid,
        "failed": failed,
    }

