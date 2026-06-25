"""API مدیریت Instanceهای WhatsApp از طریق Evolution API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core_engine.database import get_db
from core_engine.models import ChannelSession, RoleType, SessionType
from core_engine.services.evolution_service import (
    check_instance_connection,
    create_or_get_instance,
    get_instance_qr,
    logout_instance,
)
from core_engine.services.proxy_assignment_service import (
    assign_proxy_to_account,
    has_proxy_assigned,
)
from core_engine.services.rbac import requires_role

router = APIRouter(prefix="/whatsapp/evolution", tags=["evolution-whatsapp"])


class ProxyAssignRequest(BaseModel):
    host: str
    port: int
    protocol: str = "http"
    username: str | None = None
    password: str | None = None
    pool_id: str | None = None
    force: bool = False


def _get_evolution_channel_session(
    db: Session, account_id: int
) -> ChannelSession | None:
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == SessionType.EVOLUTION_INSTANCE,
        )
        .first()
    )


@router.post("/instance/{account_id}/connect")
async def connect_evolution_instance(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """ساخت یا بازیابی Instance و برگرداندن QR (در صورت نیاز)."""
    if await check_instance_connection(account_id):
        cs = _get_evolution_channel_session(db, account_id)
        return {
            "account_id": account_id,
            "already_connected": True,
            "instance_name": cs.instance_name if cs else f"mmp-whatsapp-{account_id}",
            "evolution_status": cs.evolution_status if cs else "open",
        }

    try:
        instance_data = await create_or_get_instance(account_id, db=db)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    qr_code = await get_instance_qr(account_id)
    cs = _get_evolution_channel_session(db, account_id)

    return {
        "account_id": account_id,
        "already_connected": False,
        "instance_name": cs.instance_name if cs else f"mmp-whatsapp-{account_id}",
        "evolution_status": cs.evolution_status if cs else "created",
        "instance": instance_data,
        "qr_code": qr_code,
    }


@router.get("/instance/{account_id}/status")
async def evolution_instance_status(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """وضعیت اتصال Instance و ChannelSession — بدون افشای proxy credentials."""
    connected = await check_instance_connection(account_id)
    cs = _get_evolution_channel_session(db, account_id)
    proxy_assigned = has_proxy_assigned(db, account_id)

    return {
        "account_id": account_id,
        "connected": connected,
        "proxy_assigned": proxy_assigned,
        "state": cs.evolution_status if cs else "close",
        "instance_name": cs.instance_name if cs else None,
        "evolution_status": cs.evolution_status if cs else None,
        "evolution_phone": cs.evolution_phone if cs else None,
        "evolution_profile_name": cs.evolution_profile_name if cs else None,
        "connected_at": cs.connected_at.isoformat() if cs and cs.connected_at else None,
        "disconnected_at": (
            cs.disconnected_at.isoformat() if cs and cs.disconnected_at else None
        ),
    }


@router.post("/instance/{account_id}/logout")
async def evolution_instance_logout(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))],
) -> dict[str, Any]:
    """Logout Instance — فقط admin."""
    _ = current_user
    return await logout_instance(account_id, db=db)


@router.post("/instance/{account_id}/proxy/assign")
def assign_evolution_proxy(
    account_id: int,
    body: ProxyAssignRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))],
) -> dict[str, Any]:
    """تخصیص proxy ثابت به اکانت — فقط admin؛ password در response نیست."""
    _ = current_user
    try:
        cs = assign_proxy_to_account(
            db=db,
            account_id=account_id,
            proxy_host=body.host,
            proxy_port=body.port,
            proxy_username=body.username,
            proxy_password=body.password,
            proxy_protocol=body.protocol,
            pool_id=body.pool_id,
            force=body.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "success": True,
        "account_id": account_id,
        "proxy_assigned": True,
        "proxy_protocol": cs.proxy_protocol,
        "proxy_pool_id": cs.proxy_pool_id,
        "proxy_assigned_at": (
            cs.proxy_assigned_at.isoformat() if cs.proxy_assigned_at else None
        ),
    }
