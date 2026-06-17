"""مدیریت صف ارسال پیام — dry-run و shadow mode.

Phase 4 must use database staging only. Do not call Redis worker queue push
functions from debug prepare endpoints. See core_engine.services.safety_guard.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import RenderedMessage, SendStatus
from core_engine.monitoring.metrics import increment_queued, metric_labels_from_payload
from core_engine.schemas.message import MessagePayload
from core_engine.services.consent_gate import check_consent_or_log
from core_engine.services.message_dispatch import dispatch_message, payload_dict_to_message_payload
from core_engine.services.message_log import record_message_log
from core_engine.services.message_queue_payload import (
    build_queue_payload_from_rendered_message,
)


def prepare_queue_payload(db: Session, rendered_message_id: int) -> dict:
    """ساخت payload آماده صف بدون push به Redis یا dispatch واقعی."""
    rendered_message = db.get(RenderedMessage, rendered_message_id)
    if rendered_message is None:
        raise ValueError(f"RenderedMessage {rendered_message_id} not found.")
    return build_queue_payload_from_rendered_message(rendered_message)


def enqueue_message(payload: dict[str, Any], db: Session | None = None) -> dict[str, Any]:
    """Add a message to the outbound path respecting dry-run and shadow settings."""
    blocked = check_consent_or_log(payload, db=db, source="queue_manager")
    if blocked is not None:
        return blocked

    settings = get_settings()
    message = payload_dict_to_message_payload(payload)
    original_chat_identifier = message.chat_identifier

    if settings.DRY_RUN:
        log = record_message_log(
            contact_id=message.contact_id,
            campaign_id=message.campaign_id,
            platform=message.platform,
            chat_identifier=original_chat_identifier,
            original_chat_identifier=original_chat_identifier,
            message_text=message.message_text,
            status=SendStatus.DRY_RUN,
            media_url=message.media_url,
            attempt_count=message.attempt_count,
            metadata={"source": "queue_manager"},
        )
        return {
            "enqueued": False,
            "status": SendStatus.DRY_RUN.value,
            "log": log.to_dict(),
        }

    queue_payload = message.model_dump()
    if settings.SHADOW_MODE and settings.SHADOW_PHONE_NUMBER:
        queue_payload["chat_identifier"] = settings.SHADOW_PHONE_NUMBER
        log = record_message_log(
            contact_id=message.contact_id,
            campaign_id=message.campaign_id,
            platform=message.platform,
            chat_identifier=settings.SHADOW_PHONE_NUMBER,
            original_chat_identifier=original_chat_identifier,
            message_text=message.message_text,
            status=SendStatus.SHADOW_SENT,
            media_url=message.media_url,
            attempt_count=message.attempt_count,
            metadata={"source": "queue_manager", "mode": "shadow"},
        )
        platform, account_id = metric_labels_from_payload(payload)
        increment_queued(platform, account_id)
        return {
            "enqueued": True,
            "status": SendStatus.SHADOW_SENT.value,
            "queue_payload": queue_payload,
            "original_chat_identifier": original_chat_identifier,
            "log": log.to_dict(),
        }

    log = record_message_log(
        contact_id=message.contact_id,
        campaign_id=message.campaign_id,
        platform=message.platform,
        chat_identifier=original_chat_identifier,
        original_chat_identifier=original_chat_identifier,
        message_text=message.message_text,
        status=SendStatus.QUEUED,
        media_url=message.media_url,
        attempt_count=message.attempt_count,
        metadata={"source": "queue_manager", "mode": "normal"},
    )
    platform, account_id = metric_labels_from_payload(payload)
    increment_queued(platform, account_id)
    return {
        "enqueued": True,
        "status": SendStatus.QUEUED.value,
        "queue_payload": queue_payload,
        "log": log.to_dict(),
    }


def send_message_via_queue(payload: dict[str, Any], db: Session | None = None) -> dict[str, Any]:
    """Enqueue then dispatch — used by Celery tasks."""
    enqueue_result = enqueue_message(payload, db=db)
    if not enqueue_result.get("enqueued"):
        return enqueue_result
    return dispatch_message(enqueue_result["queue_payload"])
