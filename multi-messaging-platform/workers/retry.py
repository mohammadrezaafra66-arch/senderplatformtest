"""Retry helpers for worker queue re-delivery."""

from __future__ import annotations

import json
from typing import Any

from workers.payloads import WorkerPayload, WorkerResult


def compute_retry_delay_seconds(attempt: int, base_delay_seconds: float) -> float:
    """Exponential backoff based on the next attempt number."""
    if base_delay_seconds <= 0:
        return 0.0
    exponent = max(attempt, 1) - 1
    return base_delay_seconds * (2**exponent)


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
    """Return queue JSON with incremented attempt counter."""
    data: dict[str, Any] = json.loads(raw_payload)
    data["attempt"] = payload.attempt + 1
    return json.dumps(data, ensure_ascii=False)
