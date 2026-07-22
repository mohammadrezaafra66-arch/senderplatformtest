"""شروع/توقف کمپین — وضعیت DB + کلید pause در Redis."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.models import Campaign, CampaignStatus
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.queue_bridge import push_staged_items_to_worker_queue
from core_engine.services.redis_client import get_redis_client, ping_redis
from workers.redis_keys import campaign_pause_key

logger = logging.getLogger(__name__)

_CAMPAIGN_PAUSE_VALUE = "true"
_STARTABLE_STATUSES = {
    CampaignStatus.DRAFT.value,
    CampaignStatus.PREPARED.value,
    CampaignStatus.PAUSED.value,
    CampaignStatus.RUNNING.value,
}
_STOPPABLE_STATUSES = {
    CampaignStatus.RUNNING.value,
    CampaignStatus.PAUSED.value,
    CampaignStatus.PREPARED.value,
}


class CampaignControlError(Exception):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def _require_redis() -> None:
    if not await ping_redis():
        raise CampaignControlError("Redis is unavailable", status_code=503)


async def clear_campaign_pause(campaign_id: int) -> None:
    await _require_redis()
    client = get_redis_client()
    try:
        await client.delete(campaign_pause_key(campaign_id))
    except Exception as exc:
        raise CampaignControlError("Failed to clear campaign pause in Redis", status_code=503) from exc


async def set_campaign_pause(campaign_id: int) -> None:
    await _require_redis()
    client = get_redis_client()
    try:
        await client.set(campaign_pause_key(campaign_id), _CAMPAIGN_PAUSE_VALUE)
    except Exception as exc:
        raise CampaignControlError("Failed to set campaign pause in Redis", status_code=503) from exc


def _auto_prepare(db: Session, campaign_id: int) -> None:
    """Render and stage any contacts that don't have a staged item yet.

    ``prepare_campaign_messages`` skips contacts that already have a staged
    item, so a restart never re-renders what is already staged.

    It raises ``HTTPException`` for the ordinary not-ready-yet cases (no
    contacts attached to the campaign, no valid product snapshot when the
    campaign wants products). Starting is still valid then — the campaign just
    has nothing new to stage — so those are swallowed.
    """
    try:
        result = prepare_campaign_messages(
            db,
            campaign_id,
            PrepareMessagesRequest(force_mock_output=True),
        )
    except HTTPException as exc:
        db.rollback()
        logger.info(
            "auto-prepare skipped for campaign=%s: %s", campaign_id, exc.detail
        )
        return

    logger.info(
        "auto-prepare campaign=%s staged=%s ready=%s already_staged=%s",
        campaign_id,
        result.staged_count,
        result.ready_count,
        result.already_staged_count,
    )


async def start_campaign(
    db: Session,
    campaign: Campaign,
    *,
    trigger_bridge: bool = True,
) -> dict[str, Any]:
    if campaign.status not in _STARTABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Campaign cannot be started from status '{campaign.status}'.",
        )

    already_running = campaign.status == CampaignStatus.RUNNING.value

    # Must run *before* the flip to RUNNING below: on success
    # prepare_campaign_messages sets the campaign to PREPARED and commits, and
    # the queue bridge only claims items whose campaign is already RUNNING.
    _auto_prepare(db, campaign.id)

    campaign.status = CampaignStatus.RUNNING.value
    db.flush()

    try:
        await clear_campaign_pause(campaign.id)
    except CampaignControlError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    bridge_result: dict[str, Any] | None = None
    if trigger_bridge:
        bridge_result = await push_staged_items_to_worker_queue(db, batch_size=500)

    message = "Campaign is already running." if already_running else "Campaign started successfully."
    return {
        "status": "running",
        "campaign_id": campaign.id,
        "message": message,
        "bridge_result": bridge_result,
    }


async def stop_campaign(db: Session, campaign: Campaign) -> dict[str, Any]:
    if campaign.status not in _STOPPABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Campaign cannot be stopped from status '{campaign.status}'.",
        )

    already_paused = campaign.status == CampaignStatus.PAUSED.value
    campaign.status = CampaignStatus.PAUSED.value
    db.flush()

    try:
        await set_campaign_pause(campaign.id)
    except CampaignControlError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    message = "Campaign is already paused." if already_paused else "Campaign stopped successfully."
    return {
        "status": "paused",
        "campaign_id": campaign.id,
        "message": message,
        "paused_in_redis": True,
    }
