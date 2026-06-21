"""شروع/توقف کمپین — وضعیت DB + کلید pause در Redis."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.models import Campaign, CampaignStatus
from core_engine.services.queue_bridge import push_staged_items_to_worker_queue
from core_engine.services.redis_client import get_redis_client, ping_redis
from workers.redis_keys import campaign_pause_key

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
