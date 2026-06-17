"""Phase 4 read-only staging inspection — DB summaries and Redis queue visibility."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import Campaign, CampaignStatus, Contact, StagedQueueItem
from core_engine.schemas.phase4 import (
    QueueStatusResponse,
    StagedMessagesSummaryResponse,
    StagedQueueItemResponse,
)
from core_engine.services.phase4_utils import is_consent_allowed, normalize_consent_status
from core_engine.services.redis_client import get_redis_client
from core_engine.services.safety_guard import get_safety_status


def get_campaign_staged_messages_summary(
    db: Session,
    campaign_id: int,
) -> StagedMessagesSummaryResponse:
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    contacts = (
        db.query(Contact)
        .filter(Contact.campaign_id == campaign_id)
        .order_by(Contact.id.asc())
        .all()
    )
    staged_items = (
        db.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .order_by(StagedQueueItem.id.desc())
        .all()
    )

    total_contacts = len(contacts)
    allowed_contacts = sum(1 for contact in contacts if is_consent_allowed(contact.consent_status))

    ready_staged_contact_ids = {
        item.contact_id for item in staged_items if item.status == "ready"
    }
    skipped_contacts = total_contacts - len(ready_staged_contact_ids)

    staged_count = len(staged_items)
    ready_count = sum(1 for item in staged_items if item.status == "ready")

    blocked_staged_contact_ids = {
        item.contact_id for item in staged_items if item.status == "blocked"
    }
    blocked_count = len(blocked_staged_contact_ids)
    for contact in contacts:
        if (
            normalize_consent_status(contact.consent_status) == "blocked"
            and contact.id not in blocked_staged_contact_ids
        ):
            blocked_count += 1

    return StagedMessagesSummaryResponse(
        campaign_id=campaign_id,
        total_contacts=total_contacts,
        allowed_contacts=allowed_contacts,
        skipped_contacts=skipped_contacts,
        staged_count=staged_count,
        ready_count=ready_count,
        blocked_count=blocked_count,
        items=[StagedQueueItemResponse.model_validate(item) for item in staged_items],
    )


async def inspect_redis_queue_lengths() -> dict[str, int]:
    """Read-only scan of worker queue keys — never pushes or modifies Redis."""
    client = get_redis_client()
    lengths: dict[str, int] = {}
    async for key in client.scan_iter(match="queue:*"):
        key_str = str(key)
        length = await client.llen(key_str)
        lengths[key_str] = int(length)
    return lengths


async def get_queue_status(db: Session) -> QueueStatusResponse:
    settings = get_settings()
    staged_items_count = db.query(StagedQueueItem).count()
    ready_staged_items_count = (
        db.query(StagedQueueItem)
        .filter(StagedQueueItem.status == "ready")
        .count()
    )
    campaigns_prepared_count = (
        db.query(Campaign)
        .filter(Campaign.status == CampaignStatus.PREPARED.value)
        .count()
    )

    redis_queue_lengths: dict[str, int] = {}
    redis_inspection_note = (
        "Read-only inspection. This endpoint does not push to Redis worker queues."
    )
    try:
        redis_queue_lengths = await inspect_redis_queue_lengths()
    except Exception:
        redis_queue_lengths = {}
        redis_inspection_note = (
            "Read-only inspection failed; no Redis keys were modified. "
            "This endpoint does not push to Redis worker queues."
        )

    return QueueStatusResponse(
        real_queue_push_enabled=settings.REAL_QUEUE_PUSH_ENABLED,
        redis_queue_lengths=redis_queue_lengths,
        staged_items_count=staged_items_count,
        ready_staged_items_count=ready_staged_items_count,
        campaigns_prepared_count=campaigns_prepared_count,
        safety_status=get_safety_status(),
        redis_inspection_note=redis_inspection_note,
    )
