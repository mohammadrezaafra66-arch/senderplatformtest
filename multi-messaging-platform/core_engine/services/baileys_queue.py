"""Enqueue outgoing WhatsApp jobs for Baileys via Redis list (no Node subprocess)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import Account

logger = logging.getLogger(__name__)

RAW_OUTGOING_LIST = "whatsapp:raw_outgoing"


def _digits(value: str | int | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _resolve_sender_phone(db: Session, account_id: int) -> str:
    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None:
        raise ValueError(f"Account {account_id} not found")
    phone = _digits(account.phone_number)
    if not phone:
        raise ValueError(f"Account {account_id} has no phone_number")
    return phone


def _to_jid(recipient: str) -> str:
    digits = _digits(recipient)
    if not digits:
        raise ValueError("recipient phone is empty")
    return f"{digits}@s.whatsapp.net"


def build_baileys_job(
    *,
    job_id: str,
    sender_phone: str,
    recipient: str,
    text: str,
    route: str = "campaign",
    typing_seconds: float | None = None,
    delay_after_ms: int | None = None,
) -> dict[str, Any]:
    job: dict[str, Any] = {
        "jobId": str(job_id),
        "accountId": _digits(sender_phone),
        "jid": _to_jid(recipient),
        "text": str(text).strip(),
        "route": route,
    }
    if typing_seconds is not None:
        job["typingSeconds"] = typing_seconds
    if delay_after_ms is not None:
        job["delayAfter"] = delay_after_ms
    return job


async def enqueue_baileys_job(job: dict[str, Any]) -> None:
    """RPUSH JSON job to whatsapp:raw_outgoing for Node bridge → BullMQ."""
    from core_engine.services.redis_client import get_redis_client

    payload = json.dumps(job, ensure_ascii=False)
    redis = get_redis_client()
    await redis.rpush(RAW_OUTGOING_LIST, payload)
    logger.debug("baileys job rpush jobId=%s list=%s", job.get("jobId"), RAW_OUTGOING_LIST)


def enqueue_baileys_job_sync(job: dict[str, Any]) -> None:
    """Sync RPUSH for Celery / non-async callers."""
    import redis as redis_sync

    settings = get_settings()
    client = redis_sync.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        payload = json.dumps(job, ensure_ascii=False)
        client.rpush(RAW_OUTGOING_LIST, payload)
        logger.debug("baileys job rpush (sync) jobId=%s", job.get("jobId"))
    finally:
        client.close()


async def enqueue_baileys_from_worker_payload(
    db: Session,
    payload: dict[str, Any],
    *,
    route: str = "campaign",
) -> dict[str, Any]:
    """Convert a staged/worker payload dict into a Baileys job and enqueue."""
    account_id = int(payload.get("account_id") or payload.get("accountId") or 0)
    sender_phone = _resolve_sender_phone(db, account_id)

    recipient = (
        payload.get("recipient")
        or payload.get("phone")
        or payload.get("channel_handle")
        or ""
    )
    text = payload.get("message_text") or payload.get("final_text") or ""
    message_id = payload.get("message_id") or payload.get("dedupe_key") or payload.get("jobId")

    metadata = payload.get("metadata") or {}
    if metadata.get("source") == "operational_send_test":
        route = "ui"

    job = build_baileys_job(
        job_id=str(message_id),
        sender_phone=sender_phone,
        recipient=str(recipient),
        text=str(text),
        route=route,
    )
    await enqueue_baileys_job(job)
    return job


def is_baileys_delivery_mode() -> bool:
    return get_settings().WHATSAPP_DELIVERY_MODE.strip().lower() == "baileys"
