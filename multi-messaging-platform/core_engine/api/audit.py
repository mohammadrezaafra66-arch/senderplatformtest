"""Audit log read API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core_engine.database import get_db
from core_engine.models import RoleType
from core_engine.services.audit_service import list_audit_logs
from core_engine.services.rbac import requires_role

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditLogItem(BaseModel):
    id: int
    username: str | None
    action: str
    resource_type: str
    resource_id: str
    timestamp: str
    details: dict | None = None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogItem]


@router.get("/logs", response_model=AuditLogListResponse)
def get_audit_logs(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))],
    limit: int = Query(default=50, ge=1, le=500),
):
    rows = list_audit_logs(db, limit=limit)
    return AuditLogListResponse(
        items=[
            AuditLogItem(
                id=row.id,
                username=row.username,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                timestamp=row.timestamp.isoformat(),
                details=row.details,
            )
            for row in rows
        ]
    )
