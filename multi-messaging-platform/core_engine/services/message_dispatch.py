"""Message dispatch with dry-run and shadow mode support."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from core_engine.config import get_settings
from core_engine.models import SendStatus
from core_engine.monitoring.metrics import (
    metric_labels_from_payload,
    observe_processing_time,
    record_rate_limit_hit,
    record_send_result,
)
from core_engine.schemas.message import MessagePayload
from core_engine.services.consent_gate import check_consent_or_log
from core_engine.services.message_log import MessageLog, record_message_log

logger = logging.getLogger(__name__)

SendChannelFn = Callable[[MessagePayload], dict[str, Any]]
PushQueueFn = Callable[[MessagePayload], None]

_send_to_channel: SendChannelFn | None = None
_push_to_queue: PushQueueFn | None = None


def set_send_to_channel(handler: SendChannelFn | None) -> None:
    global _send_to_channel
    _send_to_channel = handler


def set_push_to_queue(handler: PushQueueFn | None) -> None:
    global _push_to_queue
    _push_to_queue = handler


def payload_dict_to_message_payload(data: dict[str, Any]) -> MessagePayload:
    metadata = data.get("metadata") or {}
    platform = str(data.get("platform") or data.get("channel") or "").lower()
    chat_identifier = (
        data.get("chat_identifier")
        or metadata.get("customer_phone")
        or metadata.get("chat_identifier")
        or ""
    )
    return MessagePayload(
        contact_id=int(data["contact_id"]),
        campaign_id=int(data["campaign_id"]),
        platform=platform,  # type: ignore[arg-type]
        chat_identifier=str(chat_identifier),
        message_text=str(data.get("message_text") or data.get("final_text") or ""),
        media_url=data.get("media_url"),
        attempt_count=int(data.get("attempt_count") or 0),
    )


def apply_shadow_recipient(payload: MessagePayload, shadow_phone: str) -> MessagePayload:
    return payload.model_copy(update={"chat_identifier": shadow_phone})


def _is_send_success(send_result: dict[str, Any] | None) -> bool:
    if not send_result:
        return False
    if send_result.get("ok") is True:
        return True
    if send_result.get("success") is True:
        return True
    status = str(send_result.get("status", "")).lower()
    return status in {"success", "sent", "ok"}


def _send_failure_reason(send_result: dict[str, Any] | None) -> str:
    if not send_result:
        return "no_result"
    return str(
        send_result.get("error_message")
        or send_result.get("error_code")
        or send_result.get("reason")
        or send_result.get("status")
        or "unknown"
    )


def _is_rate_limit_reason(reason: str, send_result: dict[str, Any] | None) -> bool:
    haystack = reason.lower()
    if send_result:
        haystack += f" {send_result.get('error_code', '')}".lower()
        haystack += f" {send_result.get('error_message', '')}".lower()
    return "rate_limit" in haystack or "rate limit" in haystack


def _record_dispatch_metrics(
    payload: dict[str, Any],
    *,
    elapsed_seconds: float,
    is_shadow: bool,
    send_result: dict[str, Any] | None,
    channel_send_attempted: bool,
) -> None:
    platform, account_id = metric_labels_from_payload(payload)
    observe_processing_time(platform, account_id, elapsed_seconds)
    if is_shadow or not channel_send_attempted:
        return
    if _is_send_success(send_result):
        record_send_result(platform, account_id, success=True)
        return
    reason = _send_failure_reason(send_result)
    if _is_rate_limit_reason(reason, send_result):
        record_rate_limit_hit(platform, account_id)
    record_send_result(platform, account_id, success=False, reason=reason)


def dispatch_message(payload: dict[str, Any], db: Session | None = None) -> dict[str, Any]:
    """Dispatch a message honoring DRY_RUN and SHADOW_MODE settings."""
    blocked = check_consent_or_log(payload, db=db, source="message_dispatch")
    if blocked is not None:
        return blocked

    settings = get_settings()
    message = payload_dict_to_message_payload(payload)
    original_chat_identifier = message.chat_identifier
    started = time.perf_counter()
    is_shadow = bool(settings.SHADOW_MODE and settings.SHADOW_PHONE_NUMBER)

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
            metadata={"mode": "dry_run"},
        )
        return {
            "status": SendStatus.DRY_RUN.value,
            "dispatched": False,
            "log": log.to_dict(),
        }

    dispatch_payload = message
    log_status = SendStatus.QUEUED
    if is_shadow:
        dispatch_payload = apply_shadow_recipient(message, settings.SHADOW_PHONE_NUMBER)
        log_status = SendStatus.SHADOW_SENT

    try:
        if _push_to_queue is not None:
            _push_to_queue(dispatch_payload)
    except Exception as exc:
        logger.warning("queue push failed for contact %s: %s", message.contact_id, exc)
        log = record_message_log(
            contact_id=message.contact_id,
            campaign_id=message.campaign_id,
            platform=message.platform,
            chat_identifier=dispatch_payload.chat_identifier,
            original_chat_identifier=original_chat_identifier,
            message_text=message.message_text,
            status=SendStatus.FAILED_RETRYABLE,
            media_url=message.media_url,
            attempt_count=message.attempt_count,
            metadata={
                "mode": "shadow" if is_shadow else "normal",
                "stage": "queue_push",
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            },
        )
        _record_dispatch_metrics(
            payload,
            elapsed_seconds=time.perf_counter() - started,
            is_shadow=is_shadow,
            send_result=None,
            channel_send_attempted=False,
        )
        return {
            "status": SendStatus.FAILED_RETRYABLE.value,
            "dispatched": False,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "log": log.to_dict(),
        }

    send_result: dict[str, Any] | None = None
    channel_send_attempted = _send_to_channel is not None
    send_error: str | None = None
    if channel_send_attempted:
        try:
            send_result = _send_to_channel(dispatch_payload)
        except Exception as exc:
            send_error = str(exc)
            logger.warning("channel send failed for contact %s: %s", message.contact_id, exc)
            send_result = {
                "ok": False,
                "status": "failed",
                "error_message": send_error,
                "error_type": exc.__class__.__name__,
            }

    if (
        channel_send_attempted
        and not is_shadow
        and (send_error is not None or not _is_send_success(send_result))
    ):
        log_status = SendStatus.FAILED_RETRYABLE

    log = record_message_log(
        contact_id=message.contact_id,
        campaign_id=message.campaign_id,
        platform=message.platform,
        chat_identifier=dispatch_payload.chat_identifier,
        original_chat_identifier=original_chat_identifier,
        message_text=message.message_text,
        status=log_status,
        media_url=message.media_url,
        attempt_count=message.attempt_count,
        metadata={
            "mode": "shadow" if log_status == SendStatus.SHADOW_SENT else "normal",
            "send_result": send_result,
            **({"send_error": send_error} if send_error else {}),
        },
    )

    _record_dispatch_metrics(
        payload,
        elapsed_seconds=time.perf_counter() - started,
        is_shadow=is_shadow,
        send_result=send_result,
        channel_send_attempted=channel_send_attempted,
    )

    dispatched = log_status not in {SendStatus.FAILED_RETRYABLE}
    return {
        "status": log_status.value,
        "dispatched": dispatched,
        "dispatch_chat_identifier": dispatch_payload.chat_identifier,
        "original_chat_identifier": original_chat_identifier,
        "log": log.to_dict(),
    }


def build_dispatch_result_log(result: dict[str, Any]) -> MessageLog | None:
    log_data = result.get("log")
    if not log_data:
        return None
    return MessageLog(
        contact_id=log_data["contact_id"],
        campaign_id=log_data["campaign_id"],
        platform=log_data["platform"],
        chat_identifier=log_data["chat_identifier"],
        original_chat_identifier=log_data["original_chat_identifier"],
        message_text=log_data["message_text"],
        status=log_data["status"],
        media_url=log_data.get("media_url"),
        attempt_count=int(log_data.get("attempt_count") or 0),
        metadata=log_data.get("metadata") or {},
    )
