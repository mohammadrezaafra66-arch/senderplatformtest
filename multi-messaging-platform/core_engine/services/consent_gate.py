"""Shared consent gate for queue enqueue and message dispatch."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session

from core_engine.database import SessionLocal
from core_engine.models import SendStatus
from core_engine.services.consent_service import get_consent_block_reason
from core_engine.services.message_log import record_message_log

logger = logging.getLogger(__name__)


@contextmanager
def _session_scope(db: Session | None) -> Generator[Session, None, None]:
    if db is not None:
        yield db
        return

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def check_consent_or_log(
    payload: dict,
    *,
    db: Session | None = None,
    source: str,
) -> dict | None:
    """Return a blocked enqueue/dispatch result when consent is missing."""
    from core_engine.services.message_dispatch import payload_dict_to_message_payload

    message = payload_dict_to_message_payload(payload)

    try:
        with _session_scope(db) as session:
            block_reason = get_consent_block_reason(
                session,
                message.contact_id,
                message.platform,
            )
    except Exception as exc:
        logger.warning("consent check unavailable for contact %s: %s", message.contact_id, exc)
        log = record_message_log(
            contact_id=message.contact_id,
            campaign_id=message.campaign_id,
            platform=message.platform,
            chat_identifier=message.chat_identifier,
            original_chat_identifier=message.chat_identifier,
            message_text=message.message_text,
            status=SendStatus.FAILED_RETRYABLE,
            media_url=message.media_url,
            attempt_count=message.attempt_count,
            metadata={
                "source": source,
                "block_reason": "consent_check_unavailable",
                "error": str(exc),
            },
        )
        return {
            "enqueued": False,
            "dispatched": False,
            "status": SendStatus.FAILED_RETRYABLE.value,
            "error": "consent_check_unavailable",
            "log": log.to_dict(),
        }

    if block_reason is None:
        return None

    status = (
        SendStatus.BLACKLISTED
        if block_reason == "blacklisted"
        else SendStatus.OPTED_OUT
    )
    log = record_message_log(
        contact_id=message.contact_id,
        campaign_id=message.campaign_id,
        platform=message.platform,
        chat_identifier=message.chat_identifier,
        original_chat_identifier=message.chat_identifier,
        message_text=message.message_text,
        status=status,
        media_url=message.media_url,
        attempt_count=message.attempt_count,
        metadata={"source": source, "block_reason": block_reason},
    )
    return {
        "enqueued": False,
        "dispatched": False,
        "status": status.value,
        "block_reason": block_reason,
        "log": log.to_dict(),
    }
