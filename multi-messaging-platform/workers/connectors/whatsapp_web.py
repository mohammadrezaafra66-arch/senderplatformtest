"""Live WhatsApp Web connector (Playwright + persistent browser profile per account)."""

from __future__ import annotations

import logging

from core_engine.services.whatsapp_send_guard import (
    WhatsAppSendBlockedError,
    assert_whatsapp_send_allowed,
)
from core_engine.services.whatsapp_web_session import (
    load_whatsapp_web_session,
    profile_dir_has_browser_data,
    resolve_whatsapp_runtime_profile_dir,
)
from workers.config import WorkerSettings
from workers.connectors.whatsapp import resolve_whatsapp_recipient
from workers.db import get_db_session
from workers.errors import PermanentWorkerError, RetryableWorkerError, SessionInvalidError
from workers.payloads import WorkerPayload, WorkerResult
from workers.whatsapp_web.playwright_sender import send_whatsapp_web_message

logger = logging.getLogger("workers.connectors.whatsapp_web")


def load_whatsapp_web_session_metadata(account_id: int, db=None):
    owns_session = db is None
    session = db or get_db_session()
    try:
        metadata = load_whatsapp_web_session(session, int(account_id))
        if metadata is None:
            raise SessionInvalidError(
                f"No WhatsApp Web browser session found for account {account_id}."
            )
        return metadata
    finally:
        if owns_session:
            session.close()


async def deliver_whatsapp_web_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """Send a text message through WhatsApp Web (saved browser profile)."""
    try:
        await assert_whatsapp_send_allowed()
    except WhatsAppSendBlockedError as exc:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_send_disabled",
            error_message=str(exc),
            retryable=False,
        )

    try:
        session_meta = load_whatsapp_web_session_metadata(payload.account_id)
        recipient = resolve_whatsapp_recipient(payload)
    except SessionInvalidError as exc:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_web_session_missing",
            error_message=str(exc),
            retryable=False,
        )
    except PermanentWorkerError as exc:
        message = str(exc)
        error_code = (
            "whatsapp_web_recipient_invalid"
            if "recipient" in message.lower() or "phone" in message.lower()
            else "whatsapp_web_invalid_payload"
        )
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code=error_code,
            error_message=message,
            retryable=False,
        )

    if not session_meta.linked:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_web_not_linked",
            error_message="WhatsApp Web session is not marked as linked.",
            retryable=False,
        )

    profile_dir = resolve_whatsapp_runtime_profile_dir(
        int(payload.account_id),
        session_meta.profile_dir,
        profile_root=settings.WHATSAPP_WEB_PROFILE_ROOT,
    )

    if not profile_dir_has_browser_data(profile_dir):
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_web_profile_missing",
            error_message=(
                f"Browser profile directory is missing: {profile_dir}"
            ),
            retryable=False,
        )

    logger.info(
        "whatsapp_web_send_attempt account_id=%s recipient_suffix=%s profile_dir=%s",
        payload.account_id,
        recipient[-4:] if len(recipient) >= 4 else "****",
        profile_dir,
    )

    try:
        send_result = await send_whatsapp_web_message(
            profile_dir,
            recipient,
            payload.message_text,
            headless=settings.WHATSAPP_WEB_HEADLESS,
            timeout_ms=int(settings.WHATSAPP_WEB_SEND_TIMEOUT_SECONDS * 1000),
            account_id=int(payload.account_id),
            source="worker",
            message_id=payload.message_id,
            record_delivery_audit=False,
        )
    except PermanentWorkerError as exc:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_web_session_expired",
            error_message=str(exc),
            retryable=False,
        )
    except RetryableWorkerError as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_web_send_failed",
            error_message=str(exc),
            retryable=True,
        )

    return WorkerResult(
        success=True,
        status="delivered",
        platform_message_id=send_result.message_id,
        retryable=False,
    )
