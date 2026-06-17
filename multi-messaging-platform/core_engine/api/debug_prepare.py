"""Phase 4 debug endpoint for preparing campaign messages."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core_engine.api.utf8_json import utf8_json_response
from core_engine.database import get_db
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.safety_guard import SafetyViolationError, assert_phase_4_staging_safe

router = APIRouter(prefix="/debug/campaigns", tags=["debug-prepare"])


@router.post("/{campaign_id}/prepare-messages")
def debug_prepare_campaign_messages(
    campaign_id: int,
    payload: PrepareMessagesRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        assert_phase_4_staging_safe()
    except SafetyViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    result = prepare_campaign_messages(db, campaign_id, payload)
    return utf8_json_response(result.model_dump())
