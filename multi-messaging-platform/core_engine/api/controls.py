"""API کنترل‌های عملیاتی داشبورد — Kill Switch و delay حساب."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core_engine.database import get_db
from core_engine.models import RoleType
from core_engine.services.audit_service import record_audit
from core_engine.services.control_service import (
    ControlServiceError,
    get_account_delay,
    get_controls_status,
    get_kill_switch_status,
    set_account_delay,
    set_kill_switch,
)
from core_engine.services.rbac import requires_role
from core_engine.services.whatsapp_send_guard import (
    set_whatsapp_send_kill_switch,
    whatsapp_send_guard_status,
)

router = APIRouter(prefix="/controls", tags=["controls"])


class KillSwitchUpdateRequest(BaseModel):
    enabled: bool


class AccountDelayUpdateRequest(BaseModel):
    delay_seconds: int = Field(ge=1, le=3600)


def _handle_service_error(exc: ControlServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/status")
async def controls_status():
    return await get_controls_status()


@router.get("/kill-switch")
async def controls_get_kill_switch():
    try:
        return await get_kill_switch_status()
    except ControlServiceError as exc:
        _handle_service_error(exc)


@router.post("/kill-switch")
async def controls_set_kill_switch(
    payload: KillSwitchUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))],
):
    try:
        result = await set_kill_switch(payload.enabled)
    except ControlServiceError as exc:
        _handle_service_error(exc)

    record_audit(
        db,
        current_user["username"],
        "set_kill_switch",
        "controls",
        "kill_switch",
        {"enabled": payload.enabled},
    )
    db.commit()
    return result


@router.get("/whatsapp-send-kill-switch")
async def controls_get_whatsapp_send_kill_switch():
    try:
        return await whatsapp_send_guard_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/whatsapp-send-kill-switch")
async def controls_set_whatsapp_send_kill_switch(
    payload: KillSwitchUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))],
):
    try:
        result = await set_whatsapp_send_kill_switch(payload.enabled)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    record_audit(
        db,
        current_user["username"],
        "set_whatsapp_send_kill_switch",
        "controls",
        "whatsapp_send",
        {"enabled": payload.enabled, **result},
    )
    db.commit()
    return result


@router.get("/accounts/{account_id}/delay")
async def controls_get_account_delay(account_id: int):
    if account_id <= 0:
        raise HTTPException(status_code=422, detail="account_id must be positive")
    try:
        return await get_account_delay(account_id)
    except ControlServiceError as exc:
        _handle_service_error(exc)


@router.post("/accounts/{account_id}/delay")
async def controls_set_account_delay(
    account_id: int,
    payload: AccountDelayUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))],
):
    if account_id <= 0:
        raise HTTPException(status_code=422, detail="account_id must be positive")
    try:
        result = await set_account_delay(account_id, payload.delay_seconds)
    except ControlServiceError as exc:
        _handle_service_error(exc)

    record_audit(
        db,
        current_user["username"],
        "set_account_delay",
        "account",
        str(account_id),
        {"delay_seconds": payload.delay_seconds},
    )
    db.commit()
    return result
