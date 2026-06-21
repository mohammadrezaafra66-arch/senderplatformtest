"""Shared runtime helpers for single- and multi-account workers."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from workers.errors import PayloadValidationError
from workers.logging_utils import log_worker_event
from workers.payload_adapter import normalize_queue_payload
from workers.payloads import WorkerPayload

REQUIRED_PAYLOAD_FIELDS = (
    "message_id",
    "campaign_id",
    "contact_id",
    "account_id",
    "platform",
    "recipient",
    "recipient_type",
    "message_text",
    "dedupe_key",
)


def is_redis_truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, bytes):
        value = value.decode()
    return str(value).strip().lower() == "true"


def validate_worker_payload(
    raw_payload: str | dict[str, Any],
    *,
    platform: str,
    allowed_account_ids: set[str],
    logger,
) -> WorkerPayload:
    data: dict[str, Any]
    if isinstance(raw_payload, str):
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            log_worker_event(
                logger,
                event="payload_invalid_json",
                status="invalid",
                platform=platform,
                error_code="invalid_json",
                error_message=str(exc),
                level=40,
            )
            raise PayloadValidationError("Queue item is not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise PayloadValidationError("Queue item JSON must be an object.")
        data = parsed
    else:
        data = raw_payload

    data = normalize_queue_payload(data)

    missing = [field for field in REQUIRED_PAYLOAD_FIELDS if not data.get(field)]
    if missing:
        raise PayloadValidationError(f"Missing required payload fields: {', '.join(missing)}")

    try:
        payload = WorkerPayload.model_validate(data)
    except ValidationError as exc:
        raise PayloadValidationError(f"Invalid worker payload: {exc}") from exc

    if payload.platform != platform:
        raise PayloadValidationError(
            f"Payload platform '{payload.platform}' does not match worker '{platform}'."
        )
    if str(payload.account_id) not in allowed_account_ids:
        raise PayloadValidationError(
            f"Payload account_id '{payload.account_id}' is not assigned to this worker."
        )
    if not str(payload.recipient).strip():
        raise PayloadValidationError("recipient is required.")
    if not str(payload.message_text).strip():
        raise PayloadValidationError("message_text is required.")
    if not str(payload.dedupe_key).strip():
        raise PayloadValidationError("dedupe_key is required.")

    log_worker_event(
        logger,
        event="payload_validated",
        status="validated",
        message_id=payload.message_id,
        campaign_id=payload.campaign_id,
        platform=platform,
        account_id=payload.account_id,
    )
    return payload
