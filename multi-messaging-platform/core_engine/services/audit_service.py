"""Audit logging for sensitive system actions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import AuditLog


def record_audit(
    db: Session,
    username: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        username=username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
    )
    db.add(entry)
    db.flush()
    return entry


def list_audit_logs(db: Session, *, limit: int = 100) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
