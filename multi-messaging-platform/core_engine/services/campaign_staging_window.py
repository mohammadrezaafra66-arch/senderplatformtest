"""Floating ready-window. Recipients stay on the campaign; active staging does not.

PREPARE_BATCH_SIZE (400) only flushes a transaction. It is not a ready cap.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core_engine.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    MessageAttempt,
    Message,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)

STAGING_HIGH_WATER = 500
STAGING_LOW_WATER = 200
REFILL_TARGET = 500

_ACTIVE_STAGED = (
    StagedQueueItemStatus.READY.value,
    StagedQueueItemStatus.QUEUED.value,
    StagedQueueItemStatus.PUSHING.value,
)
_EXCLUDED_SEND = (
    SendStatus.QUEUED.value,
    SendStatus.PROCESSING.value,
    SendStatus.ACCEPTED_BY_WORKER.value,
    SendStatus.ACCEPTED_BY_PLATFORM.value,
    SendStatus.DELIVERED.value,
    SendStatus.READ.value,
    SendStatus.UNKNOWN_EXTERNAL_RESULT.value,
    SendStatus.FAILED_PERMANENT.value,
    SendStatus.OPTED_OUT.value,
    SendStatus.BLACKLISTED.value,
)


def refill_deficit(active: int) -> int:
    """How many new ready rows to add. 200 does not refill; 199 refills to 500."""
    active = int(active)
    if active >= STAGING_LOW_WATER:
        return 0
    return max(0, REFILL_TARGET - active)


def ready_room(active: int) -> int:
    return max(0, STAGING_HIGH_WATER - int(active))


def count_active_staged(db: Session, campaign_id: int, *, external_active: int = 0) -> int:
    stored = int(
        db.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status.in_(_ACTIVE_STAGED),
        )
        .count()
    )
    return stored + max(0, int(external_active))


def refill_campaign_staging(
    db: Session,
    campaign_id: int,
    *,
    external_active: int = 0,
) -> dict:
    """Top a running campaign back up to 500. Serialized by a row lock."""
    from core_engine.services.campaign_send_safety import pilot_blocks_refill
    from core_engine.services.phase4_prepare import prepare_campaign_messages
    from core_engine.schemas.phase4 import PrepareMessagesRequest

    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == campaign_id)
        .with_for_update()
        .first()
    )
    if campaign is None or campaign.status != CampaignStatus.RUNNING.value:
        db.rollback()
        return {"added": 0, "active": 0, "reason": "not_running"}
    if pilot_blocks_refill(db, campaign_id):
        active_now = count_active_staged(db, campaign_id)
        db.rollback()
        return {"added": 0, "active": active_now, "reason": "pilot_paused"}
    active = count_active_staged(db, campaign_id, external_active=external_active)
    deficit = refill_deficit(active)
    if deficit <= 0:
        db.rollback()
        return {"added": 0, "active": active, "reason": "above_low_water"}
    before = int(
        db.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .count()
    )
    prepare_campaign_messages(
        db,
        campaign_id,
        PrepareMessagesRequest(),
        keep_campaign_status=True,
        external_active=external_active,
    )
    after = int(
        db.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .count()
    )
    return {"added": max(0, after - before), "active": after, "reason": "refilled"}


def excluded_contact_ids(db: Session, campaign_id: int) -> set[int]:
    """Contacts that must not be staged again."""
    attempted = {
        int(row[0])
        for row in db.query(Message.contact_id)
        .join(MessageAttempt, MessageAttempt.message_id == Message.id)
        .filter(Message.campaign_id == campaign_id)
        .all()
        if row[0] is not None
    }
    sent = {
        int(row[0])
        for row in db.query(CampaignRecipient.contact_id)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.send_status.in_(_EXCLUDED_SEND),
        )
        .all()
    }
    staged = {
        int(row[0])
        for row in db.query(StagedQueueItem.contact_id)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .all()
    }
    return attempted | sent | staged
