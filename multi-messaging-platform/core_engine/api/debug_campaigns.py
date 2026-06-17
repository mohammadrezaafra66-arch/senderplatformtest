"""Phase 4 debug endpoints for campaign creation and listing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core_engine.api.utf8_json import utf8_json_response
from core_engine.database import get_db
from core_engine.models import Campaign, CampaignStatus, PlatformType
from core_engine.schemas.phase4 import CampaignCreateRequest, CampaignDebugResponse
from core_engine.services.phase4_utils import validate_campaign_channel
from core_engine.services.safety_guard import SafetyViolationError, assert_phase_4_staging_safe

router = APIRouter(prefix="/debug/campaigns", tags=["debug-campaigns"])


def _campaign_to_debug_response(campaign: Campaign) -> CampaignDebugResponse:
    return CampaignDebugResponse.model_validate(campaign)


@router.post("/create")
def debug_create_campaign(
    payload: CampaignCreateRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        assert_phase_4_staging_safe()
    except SafetyViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    channel = validate_campaign_channel(payload.channel)

    try:
        platform = PlatformType(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid channel: {channel}") from exc

    try:
        campaign = Campaign(
            name=payload.name,
            channel=channel,
            intent=payload.intent,
            message_goal=payload.message_goal,
            daily_limit=payload.daily_limit,
            max_contacts=payload.max_contacts,
            status=CampaignStatus.DRAFT.value,
            title=payload.name,
            platform=platform,
        )
        db.add(campaign)
        db.commit()
        db.refresh(campaign)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to create campaign.",
        ) from exc

    return utf8_json_response(_campaign_to_debug_response(campaign).model_dump())


@router.get("/latest")
def debug_latest_campaigns(
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=50),
):
    campaigns = (
        db.query(Campaign)
        .order_by(Campaign.id.desc())
        .limit(limit)
        .all()
    )
    items = [_campaign_to_debug_response(campaign).model_dump() for campaign in campaigns]
    return utf8_json_response(items)
