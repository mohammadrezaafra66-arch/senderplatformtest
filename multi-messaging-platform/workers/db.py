"""اتصال دیتابیس و به‌روزرسانی وضعیت ارسال Worker."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core_engine.database import SessionLocal
from core_engine.models import (
    CampaignRecipient,
    Message,
    MessageAttempt,
    MessageAttemptStatus,
    RenderedMessage,
    SendStatus,
)
from workers.logging_utils import log_worker_event as _emit_worker_log

logger = logging.getLogger("workers.db")

_RESULT_TO_SEND_STATUS: dict[str, SendStatus] = {
    "dry_run": SendStatus.DRY_RUN,
    "shadow_sent": SendStatus.SHADOW_SENT,
    "delivered": SendStatus.DELIVERED,
    "read": SendStatus.READ,
    "failed_retryable": SendStatus.FAILED_RETRYABLE,
    "failed_permanent": SendStatus.FAILED_PERMANENT,
    "placeholder_not_implemented": SendStatus.FAILED_PERMANENT,
    "live_send_disabled": SendStatus.FAILED_PERMANENT,
    "connectors_disabled": SendStatus.FAILED_PERMANENT,
    "bale_session_missing": SendStatus.FAILED_PERMANENT,
    "bale_chat_id_required": SendStatus.FAILED_PERMANENT,
    "bale_unauthorized": SendStatus.FAILED_PERMANENT,
    "bale_api_error": SendStatus.FAILED_PERMANENT,
    "bale_rate_limited": SendStatus.FAILED_RETRYABLE,
    "bale_timeout": SendStatus.FAILED_RETRYABLE,
    "bale_transport_error": SendStatus.FAILED_RETRYABLE,
    "bale_http_error": SendStatus.FAILED_RETRYABLE,
    "bale_bad_response": SendStatus.FAILED_RETRYABLE,
    "telegram_session_missing": SendStatus.FAILED_PERMANENT,
    "telegram_chat_id_required": SendStatus.FAILED_PERMANENT,
    "telegram_unauthorized": SendStatus.FAILED_PERMANENT,
    "telegram_api_error": SendStatus.FAILED_PERMANENT,
    "telegram_rate_limited": SendStatus.FAILED_RETRYABLE,
    "telegram_timeout": SendStatus.FAILED_RETRYABLE,
    "telegram_transport_error": SendStatus.FAILED_RETRYABLE,
    "telegram_http_error": SendStatus.FAILED_RETRYABLE,
    "telegram_bad_response": SendStatus.FAILED_RETRYABLE,
    "rubika_session_missing": SendStatus.FAILED_PERMANENT,
    "rubika_chat_id_required": SendStatus.FAILED_PERMANENT,
    "rubika_unauthorized": SendStatus.FAILED_PERMANENT,
    "rubika_api_error": SendStatus.FAILED_PERMANENT,
    "rubika_rate_limited": SendStatus.FAILED_RETRYABLE,
    "rubika_timeout": SendStatus.FAILED_RETRYABLE,
    "rubika_transport_error": SendStatus.FAILED_RETRYABLE,
    "rubika_http_error": SendStatus.FAILED_RETRYABLE,
    "rubika_bad_response": SendStatus.FAILED_RETRYABLE,
    "whatsapp_session_missing": SendStatus.FAILED_PERMANENT,
    "whatsapp_recipient_invalid": SendStatus.FAILED_PERMANENT,
    "whatsapp_unauthorized": SendStatus.FAILED_PERMANENT,
    "whatsapp_reengagement_required": SendStatus.FAILED_PERMANENT,
    "whatsapp_undeliverable": SendStatus.FAILED_PERMANENT,
    "whatsapp_not_registered": SendStatus.FAILED_PERMANENT,
    "whatsapp_empty_message": SendStatus.FAILED_PERMANENT,
    "whatsapp_api_error": SendStatus.FAILED_PERMANENT,
    "whatsapp_rate_limited": SendStatus.FAILED_RETRYABLE,
    "whatsapp_timeout": SendStatus.FAILED_RETRYABLE,
    "whatsapp_transport_error": SendStatus.FAILED_RETRYABLE,
    "whatsapp_http_error": SendStatus.FAILED_RETRYABLE,
    "whatsapp_bad_response": SendStatus.FAILED_RETRYABLE,
    "whatsapp_web_session_missing": SendStatus.FAILED_PERMANENT,
    "whatsapp_web_not_linked": SendStatus.FAILED_PERMANENT,
    "whatsapp_web_profile_missing": SendStatus.FAILED_PERMANENT,
    "whatsapp_web_session_expired": SendStatus.FAILED_PERMANENT,
    "whatsapp_web_recipient_invalid": SendStatus.FAILED_PERMANENT,
    "whatsapp_web_invalid_payload": SendStatus.FAILED_PERMANENT,
    "whatsapp_web_send_failed": SendStatus.FAILED_RETRYABLE,
    "whatsapp_send_throttled": SendStatus.FAILED_RETRYABLE,
    "whatsapp_hourly_cap_reached": SendStatus.FAILED_RETRYABLE,
    "whatsapp_browser_lock_busy": SendStatus.FAILED_RETRYABLE,
}

_RESULT_TO_ATTEMPT_STATUS: dict[str, MessageAttemptStatus] = {
    "dry_run": MessageAttemptStatus.DRY_RUN,
    "shadow_sent": MessageAttemptStatus.SHADOW_SENT,
    "delivered": MessageAttemptStatus.SUCCESS,
    "read": MessageAttemptStatus.SUCCESS,
    "failed_retryable": MessageAttemptStatus.FAILED_RETRYABLE,
    "failed_permanent": MessageAttemptStatus.FAILED_PERMANENT,
    "placeholder_not_implemented": MessageAttemptStatus.FAILED_PERMANENT,
    "live_send_disabled": MessageAttemptStatus.FAILED_PERMANENT,
    "connectors_disabled": MessageAttemptStatus.FAILED_PERMANENT,
    "bale_session_missing": MessageAttemptStatus.FAILED_PERMANENT,
    "bale_chat_id_required": MessageAttemptStatus.FAILED_PERMANENT,
    "bale_unauthorized": MessageAttemptStatus.FAILED_PERMANENT,
    "bale_api_error": MessageAttemptStatus.FAILED_PERMANENT,
    "bale_rate_limited": MessageAttemptStatus.FAILED_RETRYABLE,
    "bale_timeout": MessageAttemptStatus.FAILED_RETRYABLE,
    "bale_transport_error": MessageAttemptStatus.FAILED_RETRYABLE,
    "bale_http_error": MessageAttemptStatus.FAILED_RETRYABLE,
    "bale_bad_response": MessageAttemptStatus.FAILED_RETRYABLE,
    "telegram_session_missing": MessageAttemptStatus.FAILED_PERMANENT,
    "telegram_chat_id_required": MessageAttemptStatus.FAILED_PERMANENT,
    "telegram_unauthorized": MessageAttemptStatus.FAILED_PERMANENT,
    "telegram_api_error": MessageAttemptStatus.FAILED_PERMANENT,
    "telegram_rate_limited": MessageAttemptStatus.FAILED_RETRYABLE,
    "telegram_timeout": MessageAttemptStatus.FAILED_RETRYABLE,
    "telegram_transport_error": MessageAttemptStatus.FAILED_RETRYABLE,
    "telegram_http_error": MessageAttemptStatus.FAILED_RETRYABLE,
    "telegram_bad_response": MessageAttemptStatus.FAILED_RETRYABLE,
    "rubika_session_missing": MessageAttemptStatus.FAILED_PERMANENT,
    "rubika_chat_id_required": MessageAttemptStatus.FAILED_PERMANENT,
    "rubika_unauthorized": MessageAttemptStatus.FAILED_PERMANENT,
    "rubika_api_error": MessageAttemptStatus.FAILED_PERMANENT,
    "rubika_rate_limited": MessageAttemptStatus.FAILED_RETRYABLE,
    "rubika_timeout": MessageAttemptStatus.FAILED_RETRYABLE,
    "rubika_transport_error": MessageAttemptStatus.FAILED_RETRYABLE,
    "rubika_http_error": MessageAttemptStatus.FAILED_RETRYABLE,
    "rubika_bad_response": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_session_missing": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_recipient_invalid": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_unauthorized": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_reengagement_required": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_undeliverable": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_not_registered": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_empty_message": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_api_error": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_rate_limited": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_timeout": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_transport_error": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_http_error": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_bad_response": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_web_session_missing": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_web_not_linked": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_web_profile_missing": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_web_session_expired": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_web_recipient_invalid": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_web_invalid_payload": MessageAttemptStatus.FAILED_PERMANENT,
    "whatsapp_web_send_failed": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_send_throttled": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_hourly_cap_reached": MessageAttemptStatus.FAILED_RETRYABLE,
    "whatsapp_browser_lock_busy": MessageAttemptStatus.FAILED_RETRYABLE,
}


def get_db_session() -> Session:
    return SessionLocal()


def log_worker_event(
    *,
    event: str,
    platform: str | None = None,
    account_id: int | str | None = None,
    message_id: int | str | None = None,
    campaign_id: int | str | None = None,
    status: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    _emit_worker_log(
        logger,
        event=event,
        status=status,
        message_id=message_id,
        campaign_id=campaign_id,
        error_code=error_code,
        error_message=error_message,
    )
    if payload:
        logger.debug("worker_event_payload=%s", payload)


def _coerce_int(value: int | str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def update_message_attempt_result(
    *,
    message_id: int | str,
    attempt_no: int,
    status: str,
    platform_message_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    campaign_id: int | str | None = None,
    contact_id: int | str | None = None,
    account_id: int | str | None = None,
    success: bool | None = None,
    db: Session | None = None,
) -> None:
    """Persist worker send outcome to campaign_recipients and message_attempts."""
    owns_session = db is None
    session = db or SessionLocal()
    try:
        campaign_id_int = _coerce_int(campaign_id)
        contact_id_int = _coerce_int(contact_id)
        message_id_int = _coerce_int(message_id)

        send_status = _RESULT_TO_SEND_STATUS.get(status)
        if send_status is None and success is True:
            send_status = SendStatus.DELIVERED
        if send_status is None and success is False:
            send_status = SendStatus.FAILED_PERMANENT

        if campaign_id_int is not None and contact_id_int is not None:
            recipient = (
                session.query(CampaignRecipient)
                .filter(
                    CampaignRecipient.campaign_id == campaign_id_int,
                    CampaignRecipient.contact_id == contact_id_int,
                )
                .first()
            )
            if recipient is not None and send_status is not None:
                recipient.send_status = send_status
                if message_id_int is not None:
                    # message_id here is rendered_messages.id, not messages.id
                    # try to find existing Message, or create one from RenderedMessage
                    existing_message = session.get(Message, message_id_int)
                    if existing_message is None:
                        rendered = session.get(RenderedMessage, message_id_int)
                        if rendered is not None:
                            account_id_int = _coerce_int(account_id)
                            new_message = Message(
                                campaign_id=rendered.campaign_id,
                                account_id=account_id_int or 2,
                                contact_id=rendered.contact_id,
                                rendered_text=rendered.final_text,
                                dedupe_key=f"rm_{message_id_int}_{campaign_id_int}_{contact_id_int}",
                                product_snapshot_id=rendered.product_snapshot_id,
                                created_at=datetime.utcnow(),
                                updated_at=datetime.utcnow(),
                            )
                            session.add(new_message)
                            session.flush()
                            existing_message = new_message
                    if existing_message is not None:
                        recipient.final_message_id = existing_message.id

        attempt_status = _RESULT_TO_ATTEMPT_STATUS.get(status)
        if attempt_status is None and success is True:
            attempt_status = MessageAttemptStatus.SUCCESS
        if attempt_status is None and success is False:
            attempt_status = MessageAttemptStatus.FAILED_PERMANENT

        if message_id_int is not None and attempt_status is not None:
            message = session.get(Message, message_id_int)
            if message is not None:
                attempt = MessageAttempt(
                    message_id=message_id_int,
                    attempt_no=attempt_no,
                    status=attempt_status,
                    started_at=datetime.utcnow(),
                    accepted_at=datetime.utcnow() if success else None,
                    platform_message_id=platform_message_id,
                    error_code=error_code,
                    error_message=error_message,
                )
                session.add(attempt)

        session.commit()
        log_worker_event(
            event="message_attempt_result_saved",
            message_id=message_id,
            campaign_id=campaign_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            payload={
                "attempt_no": attempt_no,
                "platform_message_id": platform_message_id,
                "contact_id": contact_id,
            },
        )
    except Exception as exc:
        session.rollback()
        log_worker_event(
            event="message_attempt_result_failed",
            message_id=message_id,
            campaign_id=campaign_id,
            status=status,
            error_code="db_persist_failed",
            error_message=str(exc),
        )
        raise
    finally:
        if owns_session:
            session.close()
