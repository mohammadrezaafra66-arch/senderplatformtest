"""Audit trail for message delivery attempts (UI, worker, scripts)."""

from __future__ import annotations

import logging
from typing import Any

from core_engine.database import SessionLocal
from core_engine.services.audit_service import record_audit

logger = logging.getLogger(__name__)


def record_whatsapp_delivery_audit(
    *,
    source: str,
    account_id: int | str,
    recipient: str,
    message_id: str | None = None,
    message_text: str | None = None,
    success: bool,
    status: str,
    platform_message_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    username: str | None = None,
    extra: dict[str, Any] | None = None,
    session=None,
) -> None:
    """Persist a WhatsApp delivery attempt to audit_logs (works outside FastAPI requests)."""
    actor = username or f"system:{source}"
    details: dict[str, Any] = {
        "source": source,
        "account_id": int(account_id) if str(account_id).isdigit() else account_id,
        "recipient": recipient,
        "message_id": message_id,
        "message_text": message_text,
        "success": success,
        "status": status,
        "platform_message_id": platform_message_id,
        "error_code": error_code,
        "error_message": error_message,
    }
    if extra:
        details.update(extra)

    owns_session = session is None
    db = session or SessionLocal()
    try:
        record_audit(
            db,
            actor,
            "whatsapp_delivery",
            "account",
            str(account_id),
            details,
        )
        if owns_session:
            db.commit()
    except Exception:
        logger.exception(
            "whatsapp_delivery_audit_failed account_id=%s recipient_suffix=%s status=%s",
            account_id,
            recipient[-4:] if len(recipient) >= 4 else "****",
            status,
        )
        if owns_session:
            db.rollback()
    finally:
        if owns_session:
            db.close()


def record_worker_whatsapp_delivery(payload, result) -> None:
    """Audit a WhatsApp worker queue delivery attempt (UI ops test or campaign)."""
    metadata = getattr(payload, "metadata", None) or {}
    origin = metadata.get("source") or metadata.get("origin") or "worker_queue"
    route = "ui" if origin == "operational_send_test" else str(origin)

    record_whatsapp_delivery_audit(
        source="worker",
        account_id=payload.account_id,
        recipient=str(payload.recipient),
        message_id=str(payload.message_id),
        message_text=payload.message_text,
        success=result.success,
        status=result.status,
        platform_message_id=result.platform_message_id,
        error_code=result.error_code,
        error_message=result.error_message,
        extra={"route": route},
    )
