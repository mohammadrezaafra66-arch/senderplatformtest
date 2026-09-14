"""Retry helpers for worker queue re-delivery."""

from __future__ import annotations

import json
from typing import Any

from workers.payloads import WorkerPayload, WorkerResult


def compute_retry_delay_seconds(
    attempt: int,
    base_delay_seconds: float,
    *,
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
) -> float:
    """Exponential backoff, or policy-aware delay when error_code is known."""
    from core_engine.services.campaign_retry_schedule import compute_policy_retry_delay_seconds

    delay, _delayed = compute_policy_retry_delay_seconds(
        attempt,
        base_delay_seconds,
        error_code=error_code,
        details=details,
    )
    return delay


def retry_should_use_delayed_queue(
    attempt: int,
    base_delay_seconds: float,
    *,
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
) -> bool:
    from core_engine.services.campaign_retry_schedule import compute_policy_retry_delay_seconds

    _delay, delayed = compute_policy_retry_delay_seconds(
        attempt,
        base_delay_seconds,
        error_code=error_code,
        details=details,
    )
    return delayed


def should_schedule_retry(
    payload: WorkerPayload,
    result: WorkerResult,
    *,
    max_retry_attempts: int,
) -> bool:
    if max_retry_attempts <= 0:
        return False
    if not result.retryable:
        return False
    return payload.attempt < max_retry_attempts


def build_retry_queue_payload(raw_payload: str, payload: WorkerPayload) -> str:
    """Increment attempt. Replay text already submitted to a connector.

    If the live connector was not entered, message_text and metadata stay as
    queued. Delivery refreshes AfraKala again before the next outbound render.
    If external_send_submitted is set, the submitted text is kept so a retry
    cannot become a second, different external message.
    """
    data: dict[str, Any] = json.loads(raw_payload)
    data["attempt"] = payload.attempt + 1
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    if metadata.get("external_send_submitted") is True:
        data["message_text"] = payload.message_text
        data["metadata"] = metadata
    return json.dumps(data, ensure_ascii=False)
