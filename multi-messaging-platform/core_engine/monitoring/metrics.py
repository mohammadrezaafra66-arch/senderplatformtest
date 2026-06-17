"""Prometheus KPI metrics for queueing, sending, and rate limits."""

from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Histogram

MESSAGES_QUEUED = Counter(
    "messages_queued_total",
    "Total messages enqueued",
    ["platform", "account_id"],
)

MESSAGES_SENT_SUCCESS = Counter(
    "messages_sent_success_total",
    "Total successfully sent messages",
    ["platform", "account_id"],
)

MESSAGES_SENT_FAILED = Counter(
    "messages_sent_failed_total",
    "Total failed message sends",
    ["platform", "account_id", "reason"],
)

RATE_LIMIT_HITS = Counter(
    "rate_limit_hits_total",
    "Total rate limit hits",
    ["platform", "account_id"],
)

MESSAGE_PROCESSING_TIME = Histogram(
    "message_processing_seconds",
    "Message processing time in seconds",
    ["platform", "account_id"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


def metric_labels_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    platform = str(payload.get("platform") or payload.get("channel") or "unknown").lower()
    metadata = payload.get("metadata") or {}
    account_id = str(payload.get("account_id") or metadata.get("account_id") or "0")
    return platform, account_id


def increment_queued(platform: str, account_id: str | int) -> None:
    MESSAGES_QUEUED.labels(platform=str(platform).lower(), account_id=str(account_id)).inc()


def record_send_result(
    platform: str,
    account_id: str | int,
    *,
    success: bool = True,
    reason: str = "",
) -> None:
    labels = {"platform": str(platform).lower(), "account_id": str(account_id)}
    if success:
        MESSAGES_SENT_SUCCESS.labels(**labels).inc()
    else:
        MESSAGES_SENT_FAILED.labels(**labels, reason=reason or "unknown").inc()


def record_rate_limit_hit(platform: str, account_id: str | int) -> None:
    RATE_LIMIT_HITS.labels(platform=str(platform).lower(), account_id=str(account_id)).inc()


def observe_processing_time(
    platform: str,
    account_id: str | int,
    elapsed_seconds: float,
) -> None:
    MESSAGE_PROCESSING_TIME.labels(
        platform=str(platform).lower(),
        account_id=str(account_id),
    ).observe(elapsed_seconds)
