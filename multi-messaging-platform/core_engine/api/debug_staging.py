"""Phase 4 read-only debug endpoints for staged messages and queue status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core_engine.api.utf8_json import utf8_json_response
from core_engine.database import get_db
from core_engine.services.phase4_staging import (
    get_campaign_staged_messages_summary,
    get_queue_status,
)
from core_engine.services.safety_guard import SafetyViolationError, assert_phase_4_staging_safe

router = APIRouter(tags=["debug-staging"])


@router.get("/debug/campaigns/{campaign_id}/staged-messages")
def debug_campaign_staged_messages(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        assert_phase_4_staging_safe()
    except SafetyViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    summary = get_campaign_staged_messages_summary(db, campaign_id)
    return utf8_json_response(summary.model_dump())


@router.get("/debug/queue/status")
async def debug_queue_status(
    db: Annotated[Session, Depends(get_db)],
):
    try:
        assert_phase_4_staging_safe()
    except SafetyViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    status = await get_queue_status(db)
    return utf8_json_response(status.model_dump())
