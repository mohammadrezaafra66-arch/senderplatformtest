"""Consume Baileys delivery results and session status from Redis into PostgreSQL."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core_engine.database import SessionLocal
from core_engine.models import Account, AuditLog

logger = logging.getLogger(__name__)

RESULTS_LIST = "whatsapp:results"
SESSION_STATUS_LIST = "whatsapp:session_status"
MAX_BATCH = 200


def _parse_json(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        logger.warning("invalid baileys redis json: %s", raw[:200])
    return None


def _status_to_success(status: str) -> bool:
    return status == "delivered"


def _recipient_digits(item: dict[str, Any]) -> str:
    recipient = item.get("jid") or item.get("recipient") or ""
    if "@" in str(recipient):
        return str(recipient).split("@", 1)[0]
    return _digits_only(recipient)


def _digits_only(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _build_audit_row(item: dict[str, Any]) -> AuditLog:
    status = str(item.get("status") or "unknown")
    success = _status_to_success(status)
    account_id = item.get("accountId") or item.get("account_id") or "unknown"
    recipient = _recipient_digits(item)

    details: dict[str, Any] = {
        "source": "baileys",
        "account_id": int(account_id) if str(account_id).isdigit() else account_id,
        "recipient": recipient,
        "message_id": item.get("jobId") or item.get("message_id"),
        "message_text": item.get("message_text") or item.get("text"),
        "success": success,
        "status": status,
        "platform_message_id": item.get("platform_message_id"),
        "error_code": item.get("error") if not success else None,
        "error_message": item.get("error") if not success else None,
        "route": item.get("route"),
        "timestamp": item.get("timestamp"),
        "jid": item.get("jid"),
    }

    return AuditLog(
        username="system:baileys",
        action="whatsapp_delivery",
        resource_type="account",
        resource_id=str(account_id),
        details=details,
        timestamp=datetime.utcnow(),
    )


def _bulk_insert_audits(db: Session, rows: list[AuditLog]) -> None:
    if not rows:
        return
    db.add_all(rows)
    db.flush()


def consume_whatsapp_results_batch(
    *,
    redis_client=None,
    batch_size: int = MAX_BATCH,
) -> dict[str, int]:
    """Read results from Redis, bulk-insert audit_logs, trim list after commit."""
    import redis as redis_sync

    from core_engine.config import get_settings

    settings = get_settings()
    owns_client = redis_client is None
    client = redis_client or redis_sync.from_url(settings.REDIS_URL, decode_responses=True)

    processed = 0
    failed = 0
    skipped = 0

    try:
        raw_items = client.lrange(RESULTS_LIST, 0, batch_size - 1)
        if not raw_items:
            return {"processed": 0, "failed": 0, "skipped": 0}

        audit_rows: list[AuditLog] = []
        for raw in raw_items:
            item = _parse_json(raw)
            if item is None:
                failed += 1
                continue
            try:
                audit_rows.append(_build_audit_row(item))
                processed += 1
            except Exception:
                logger.exception(
                    "failed to build audit row jobId=%s",
                    item.get("jobId"),
                )
                failed += 1

        if not audit_rows:
            client.ltrim(RESULTS_LIST, len(raw_items), -1)
            return {"processed": 0, "failed": failed, "skipped": skipped}

        db = SessionLocal()
        try:
            _bulk_insert_audits(db, audit_rows)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("bulk audit insert failed — redis list unchanged")
            raise
        finally:
            db.close()

        pipe = client.pipeline()
        pipe.ltrim(RESULTS_LIST, len(raw_items), -1)
        pipe.execute()

    finally:
        if owns_client:
            client.close()

    if processed:
        logger.info(
            "baileys results consumed processed=%s failed=%s",
            processed,
            failed,
        )
    return {"processed": processed, "failed": failed, "skipped": skipped}


def consume_whatsapp_session_status_batch(
    *,
    redis_client=None,
    batch_size: int = 100,
) -> dict[str, int]:
    """Mark accounts disconnected when Baileys reports session_invalid."""
    import redis as redis_sync

    from core_engine.config import get_settings
    from core_engine.services.baileys_session_service import mark_baileys_session_disconnected

    settings = get_settings()
    owns_client = redis_client is None
    client = redis_client or redis_sync.from_url(settings.REDIS_URL, decode_responses=True)

    processed = 0
    failed = 0

    try:
        raw_items = client.lrange(SESSION_STATUS_LIST, 0, batch_size - 1)
        if not raw_items:
            return {"processed": 0, "failed": 0}

        db = SessionLocal()
        try:
            for raw in raw_items:
                item = _parse_json(raw)
                if item is None:
                    failed += 1
                    continue
                status = str(item.get("status") or item.get("type") or "")
                if status not in {"session_invalid", "disconnected", "session_invalid_401"}:
                    failed += 1
                    continue
                try:
                    phone = _digits_only(item.get("accountId") or item.get("account_id"))
                    reason = str(item.get("reason") or item.get("error") or status)
                    if mark_baileys_session_disconnected(db, phone, reason=reason):
                        processed += 1
                    else:
                        failed += 1
                except Exception:
                    logger.exception(
                        "session status handler failed accountId=%s",
                        item.get("accountId"),
                    )
                    failed += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        pipe = client.pipeline()
        pipe.ltrim(SESSION_STATUS_LIST, len(raw_items), -1)
        pipe.execute()
    finally:
        if owns_client:
            client.close()

    if processed:
        logger.info("baileys session status processed=%s failed=%s", processed, failed)
    return {"processed": processed, "failed": failed}
