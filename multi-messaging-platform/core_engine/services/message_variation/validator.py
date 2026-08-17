"""Validate GPT variations before they enter the render pipeline."""

from __future__ import annotations

import re

from core_engine.services.message_variation.errors import (
    GPT_INSUFFICIENT_VARIATIONS,
    GPT_VARIATION_INVALID,
    GptVariationError,
)
from core_engine.services.message_variation.placeholders import (
    canonical_placeholder_set,
    has_malformed_placeholders,
)
from core_engine.services.product_feed.composition import CONTROLLED_HEADINGS

_WHITESPACE = re.compile(r"\s+")


def normalize_variation_text(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def validate_message_variation(
    text: str,
    *,
    expected_placeholders: frozenset[str],
    min_chars: int,
    max_chars: int,
) -> list[str]:
    reasons: list[str] = []
    body = (text or "").strip()
    if not body:
        reasons.append("empty")
        return reasons
    if len(body) < min_chars:
        reasons.append("too_short")
    if len(body) > max_chars:
        reasons.append("too_long")
    if has_malformed_placeholders(body, expected_names=expected_placeholders):
        reasons.append("malformed_placeholder")
    actual = canonical_placeholder_set(body)
    missing = expected_placeholders - actual
    extra = actual - expected_placeholders
    if missing:
        reasons.append("missing_placeholder")
    if extra:
        reasons.append("unknown_placeholder")
    for heading in CONTROLLED_HEADINGS:
        if heading in body:
            reasons.append("product_heading_injection")
            break
    return reasons


def validate_variation_set(
    texts: list[str],
    *,
    expected_placeholders: frozenset[str],
    min_count: int,
    min_chars: int,
    max_chars: int,
) -> None:
    cleaned = [item.strip() for item in texts if isinstance(item, str)]
    if len(cleaned) < min_count:
        raise GptVariationError(
            GPT_INSUFFICIENT_VARIATIONS,
            details={"got": len(cleaned), "required": min_count},
        )
    seen: set[str] = set()
    for text in cleaned:
        reasons = validate_message_variation(
            text,
            expected_placeholders=expected_placeholders,
            min_chars=min_chars,
            max_chars=max_chars,
        )
        if reasons:
            raise GptVariationError(
                GPT_VARIATION_INVALID,
                details={"reasons": reasons},
            )
        key = normalize_variation_text(text)
        if key in seen:
            raise GptVariationError(
                GPT_VARIATION_INVALID,
                details={"reasons": ["duplicate"]},
            )
        seen.add(key)
