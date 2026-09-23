"""Official reprepare for a campaign that has not started sending.

Resets only unsent staged rows, then prepares the unique audience again in
the original recipient order. The whole change commits once inside prepare.
A rejection or a later failure rolls the reset back.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from core_engine.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Message,
    MessageAttempt,
    RenderedMessage,
    RenderStatus,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.message_variation.service import pool_from_queue_payload
from core_engine.services.phase4_prepare import PREPARE_BATCH_SIZE, prepare_campaign_messages
from core_engine.services.product_feed.service import snapshot_from_queue_payload

_UNSENT_STAGED = (
    StagedQueueItemStatus.STAGED.value,
    StagedQueueItemStatus.BLOCKED.value,
    StagedQueueItemStatus.SKIPPED.value,
    StagedQueueItemStatus.READY.value,
)
_QUEUED_STAGED = (
    StagedQueueItemStatus.QUEUED.value,
    StagedQueueItemStatus.PUSHING.value,
)
_SENT_RECIPIENT = (
    SendStatus.QUEUED.value,
    SendStatus.PROCESSING.value,
    SendStatus.ACCEPTED_BY_WORKER.value,
    SendStatus.ACCEPTED_BY_PLATFORM.value,
    SendStatus.DELIVERED.value,
    SendStatus.READ.value,
    SendStatus.UNKNOWN_EXTERNAL_RESULT.value,
)


class ReprepareRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def reprepare_campaign(
    db: Session,
    campaign_id: int,
    *,
    activity_probe: Callable[[int], str | None] | None = None,
) -> dict:
    """Reset unsent staging and prepare again. Does not start the campaign."""
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign is None:
        raise ReprepareRejected("CAMPAIGN_NOT_FOUND", "Campaign not found.")
    if campaign.status == CampaignStatus.RUNNING.value:
        raise ReprepareRejected(
            "CAMPAIGN_RUNNING",
            "کمپین در حال اجرا را نمی‌توان دوباره آماده کرد.",
        )
    if campaign.status not in {
        CampaignStatus.DRAFT.value,
        CampaignStatus.PREPARED.value,
        CampaignStatus.PAUSED.value,
    }:
        raise ReprepareRejected(
            "CAMPAIGN_STATUS_NOT_REPREPAREABLE",
            "این کمپین در وضعیت فعلی قابل آماده‌سازی دوباره نیست.",
        )

    if _campaign_has_attempts(db, campaign_id):
        raise ReprepareRejected(
            "CAMPAIGN_HAS_ATTEMPTS",
            "کمپینی که Attempt یا ارسال موفق دارد دوباره آماده نمی‌شود.",
        )
    if _campaign_has_sent_recipients(db, campaign_id):
        raise ReprepareRejected(
            "CAMPAIGN_HAS_SENDS",
            "کمپینی که ارسال موفق دارد دوباره آماده نمی‌شود.",
        )
    if _queued_staged_count(db, campaign_id):
        raise ReprepareRejected(
            "CAMPAIGN_QUEUE_NONEMPTY",
            "Queue این کمپین خالی نیست.",
        )

    probe = activity_probe if activity_probe is not None else _redis_activity_probe
    activity = probe(campaign_id)
    if activity:
        raise ReprepareRejected(
            "CAMPAIGN_LIVE_ACTIVITY",
            "Queue، Lease یا Inflight این کمپین خالی نیست.",
        )

    preserved_pool, preserved_snapshot = _capture_render_locks(db, campaign_id)
    reset_count = _reset_unsent_staged(db, campaign_id)
    result = prepare_campaign_messages(
        db,
        campaign_id,
        PrepareMessagesRequest(),
        preserved_gpt_pool=preserved_pool,
        preserved_product_snapshot=preserved_snapshot,
    )
    return {
        "campaign_id": campaign_id,
        "reset_unsent_staged": reset_count,
        "ready_count": result.ready_count,
        "staged_count": result.staged_count,
        "allowed_contacts": result.allowed_contacts,
        "total_contacts": result.total_contacts,
        "real_gpt_called": result.real_gpt_called,
        "redis_queue_pushed": result.redis_queue_pushed,
        "status": CampaignStatus.PREPARED.value,
    }


def _campaign_has_attempts(db: Session, campaign_id: int) -> bool:
    return (
        db.query(MessageAttempt.id)
        .join(Message, Message.id == MessageAttempt.message_id)
        .filter(Message.campaign_id == campaign_id)
        .first()
        is not None
    )


def _campaign_has_sent_recipients(db: Session, campaign_id: int) -> bool:
    return (
        db.query(CampaignRecipient.id)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.send_status.in_(_SENT_RECIPIENT),
        )
        .first()
        is not None
    )


def _queued_staged_count(db: Session, campaign_id: int) -> int:
    return int(
        db.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status.in_(_QUEUED_STAGED),
        )
        .count()
    )


def _redis_activity_probe(campaign_id: int) -> str | None:
    """Read-only lookup. A connection failure rejects reprepare."""
    from redis import Redis

    from core_engine.config import get_settings
    from workers.redis_keys import (
        queue_key,
        rubika_inflight_member_key,
        rubika_send_lease_key,
    )
    from core_engine.database import SessionLocal
    from core_engine.models import CampaignAccount

    db = SessionLocal()
    try:
        message_ids = [
            int(row[0])
            for row in db.query(Message.id).filter(Message.campaign_id == campaign_id).all()
        ]
        account_ids = [
            int(row[0])
            for row in db.query(CampaignAccount.account_id)
            .filter(CampaignAccount.campaign_id == campaign_id)
            .all()
        ]
    finally:
        db.close()

    client = Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    try:
        for message_id in message_ids:
            if client.exists(rubika_send_lease_key(message_id)):
                return "lease"
            for account_id in account_ids:
                if client.exists(rubika_inflight_member_key(account_id, message_id)):
                    return "inflight"
        marker = f'"campaign_id": {campaign_id}'
        marker_compact = f'"campaign_id":{campaign_id}'
        for account_id in account_ids:
            for raw in client.lrange(queue_key("rubika", account_id), 0, -1):
                if marker in raw or marker_compact in raw:
                    return "queue"
    finally:
        client.close()
    return None


def _capture_render_locks(db: Session, campaign_id: int):
    pool = None
    snapshot = None
    items = (
        db.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .order_by(StagedQueueItem.id.asc())
        .all()
    )
    for item in items:
        if pool is None:
            pool = pool_from_queue_payload(item.queue_payload)
        if snapshot is None:
            snapshot = snapshot_from_queue_payload(item.queue_payload)
        if pool is not None and snapshot is not None:
            break
    return pool, snapshot


def _reset_unsent_staged(db: Session, campaign_id: int) -> int:
    reset_count = 0
    while True:
        items = (
            db.query(StagedQueueItem)
            .filter(
                StagedQueueItem.campaign_id == campaign_id,
                StagedQueueItem.status.in_(_UNSENT_STAGED),
            )
            .order_by(StagedQueueItem.id.asc())
            .limit(PREPARE_BATCH_SIZE)
            .all()
        )
        if not items:
            break
        contact_ids = [int(item.contact_id) for item in items]
        rendered_ids = [
            int(item.rendered_message_id)
            for item in items
            if item.rendered_message_id is not None
        ]
        for item in items:
            db.delete(item)
        reset_count += len(items)
        db.flush()
        if rendered_ids:
            (
                db.query(RenderedMessage)
                .filter(RenderedMessage.id.in_(rendered_ids))
                .delete(synchronize_session=False)
            )
        recipients = (
            db.query(CampaignRecipient)
            .filter(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.contact_id.in_(contact_ids),
            )
            .all()
        )
        message_ids = [
            int(recipient.final_message_id)
            for recipient in recipients
            if recipient.final_message_id is not None
        ]
        for recipient in recipients:
            recipient.final_message_id = None
            recipient.render_status = RenderStatus.PENDING
        db.flush()
        if message_ids:
            (
                db.query(Message)
                .filter(
                    Message.id.in_(message_ids),
                    Message.campaign_id == campaign_id,
                )
                .delete(synchronize_session=False)
            )
        db.flush()
    return reset_count
