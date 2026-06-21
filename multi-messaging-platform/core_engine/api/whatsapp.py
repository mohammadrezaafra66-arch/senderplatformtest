"""WhatsApp operational API — warmup trigger and related admin actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core_engine.database import get_db
from core_engine.models import RoleType
from core_engine.services.audit_service import record_audit
from core_engine.services.rbac import requires_role
from core_engine.services.whatsapp_warmup import trigger_whatsapp_warmup

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.post("/trigger-warmup")
async def whatsapp_trigger_warmup(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))],
):
    """Schedule cross-line warmup matrix (admin only, 24h debounce)."""
    result = await trigger_whatsapp_warmup()

    record_audit(
        db,
        current_user["username"],
        "trigger_whatsapp_warmup",
        "whatsapp",
        "warmup",
        {
            "pairedAccounts": result.get("pairedAccounts"),
            "totalJobs": result.get("totalJobs"),
        },
    )
    db.commit()
    return result
