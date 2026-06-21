"""Normalize staged/legacy queue dicts into WorkerPayload-compatible data."""

from __future__ import annotations

from typing import Any


def normalize_queue_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Map queue_bridge / staging payloads to worker payload fields."""
    normalized = dict(data)

    campaign_id = normalized.get("campaign_id")
    contact_id = normalized.get("contact_id")
    platform = (
        normalized.get("platform")
        or normalized.get("channel")
        or ""
    )
    normalized["platform"] = str(platform).strip().lower()

    message_text = normalized.get("message_text") or normalized.get("final_text") or ""
    normalized["message_text"] = str(message_text)

    recipient = (
        normalized.get("recipient")
        or normalized.get("phone")
        or normalized.get("channel_handle")
        or ""
    )
    normalized["recipient"] = str(recipient).strip()

    if not normalized.get("recipient_type"):
        normalized["recipient_type"] = (
            "phone_number" if normalized.get("phone") else "channel_handle"
        )

    if not normalized.get("message_id"):
        rendered_id = normalized.get("rendered_message_id")
        if rendered_id is not None:
            normalized["message_id"] = rendered_id
        elif campaign_id is not None and contact_id is not None:
            normalized["message_id"] = f"{campaign_id}:{contact_id}"

    if not normalized.get("dedupe_key") and campaign_id is not None and contact_id is not None:
        attempt = int(normalized.get("attempt") or 1)
        normalized["dedupe_key"] = f"c{campaign_id}-ct{contact_id}-a{attempt}"

    channel_handle = normalized.get("channel_handle")
    if channel_handle:
        metadata = dict(normalized.get("metadata") or {})
        metadata.setdefault("channel_handle", str(channel_handle).strip())
        normalized["metadata"] = metadata

    if normalized["platform"] in ("bale", "telegram", "rubika") and channel_handle:
        normalized["recipient"] = str(channel_handle).strip()
        normalized["recipient_type"] = "channel_handle"

    phone = normalized.get("phone")
    if normalized["platform"] == "whatsapp" and phone:
        normalized["recipient"] = str(phone).strip()
        normalized["recipient_type"] = "phone_number"

    return normalized
