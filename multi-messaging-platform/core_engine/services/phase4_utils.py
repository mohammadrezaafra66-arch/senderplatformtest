"""Phase 4 utility helpers — normalization and staging payload builders.

These functions are pure and safe: no Redis, workers, GPT, or external channels.
"""

from __future__ import annotations

import re
from typing import Any

from core_engine.config import get_settings
from core_engine.services.pricing_scraper import normalize_persian_digits

PHASE4_CHANNELS = frozenset({"whatsapp", "telegram", "rubika", "bale"})
CONSENT_ALLOWED = "allowed"
CONSENT_BLOCKED = "blocked"
CONSENT_UNKNOWN = "unknown"

_LEGACY_CONSENT_MAP = {
    "opted_in": CONSENT_ALLOWED,
    "opted_out": CONSENT_BLOCKED,
    CONSENT_ALLOWED: CONSENT_ALLOWED,
    CONSENT_BLOCKED: CONSENT_BLOCKED,
    CONSENT_UNKNOWN: CONSENT_UNKNOWN,
}


def _looks_like_iranian_mobile_with_leading_zero(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return len(digits) == 11 and digits.startswith("09")


def normalize_phone(phone: str) -> str:
    """Normalize a phone string for storage and staging payloads."""
    if phone is None or not str(phone).strip():
        raise ValueError("phone is required and must be non-empty")

    text = normalize_persian_digits(str(phone).strip())
    for separator in (" ", "-", "(", ")"):
        text = text.replace(separator, "")

    if not text:
        raise ValueError("phone is required and must be non-empty")

    if text.startswith("0098"):
        normalized = "+98" + text[4:]
    elif text.startswith("+"):
        normalized = "+" + re.sub(r"\D", "", text[1:])
    elif text.startswith("98"):
        normalized = "+98" + re.sub(r"\D", "", text[2:])
    elif text.startswith("0") and _looks_like_iranian_mobile_with_leading_zero(text):
        normalized = "+98" + re.sub(r"\D", "", text[1:])
    elif re.sub(r"\D", "", text).startswith("9") and len(re.sub(r"\D", "", text)) == 10:
        normalized = "+98" + re.sub(r"\D", "", text)
    else:
        digits = re.sub(r"\D", "", text)
        if not digits:
            raise ValueError("phone has no digits")
        normalized = "+" + digits if text.startswith("+") else "+" + digits

    return normalized


def build_full_name(
    first_name: str | None,
    last_name: str | None,
) -> str | None:
    """Join trimmed first/last names, or return None when both are empty."""
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    if not first and not last:
        return None
    if first and last:
        return f"{first} {last}"
    return first or last


def normalize_consent_status(value: str | None) -> str:
    """Map legacy and Phase 4 consent values to the standard set."""
    if value is None or not str(value).strip():
        return CONSENT_UNKNOWN

    normalized = str(value).strip().lower()
    mapped = _LEGACY_CONSENT_MAP.get(normalized)
    if mapped is None:
        raise ValueError(
            "consent_status must be one of: allowed, blocked, unknown "
            f"(legacy opted_in/opted_out also accepted); got {value!r}"
        )
    return mapped


def is_consent_allowed(value: str) -> bool:
    """Return True only when consent is explicitly allowed."""
    return normalize_consent_status(value) == CONSENT_ALLOWED


def validate_campaign_channel(channel: str) -> str:
    """Lowercase, trim, and validate a Phase 4 campaign channel."""
    if channel is None or not str(channel).strip():
        raise ValueError("channel is required")

    normalized = str(channel).strip().lower()
    if normalized not in PHASE4_CHANNELS:
        raise ValueError(
            "channel must be one of: whatsapp, telegram, rubika, bale; "
            f"got {channel!r}"
        )
    return normalized


def build_staged_queue_payload(
    *,
    campaign_id: int,
    contact_id: int,
    rendered_message_id: int | None,
    channel: str,
    phone: str,
    channel_handle: str | None,
    final_text: str,
) -> dict[str, Any]:
    """Build a pure dict for DB staging — never pushes to Redis or workers."""
    settings = get_settings()
    return {
        "campaign_id": campaign_id,
        "contact_id": contact_id,
        "rendered_message_id": rendered_message_id,
        "channel": validate_campaign_channel(channel),
        "phone": normalize_phone(phone),
        "channel_handle": channel_handle,
        "final_text": final_text,
        "dry_run": True,
        "real_queue_push_enabled": settings.REAL_QUEUE_PUSH_ENABLED,
        "ready_for_queue": True,
        "safety_note": "DB staging only. Not pushed to Redis.",
    }
