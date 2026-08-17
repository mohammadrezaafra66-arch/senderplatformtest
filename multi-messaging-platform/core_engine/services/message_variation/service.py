"""Campaign GPT variation pool generation, preview, and freeze."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from core_engine.config import get_settings
from core_engine.services.message_variation.assignment import assign_variation
from core_engine.services.message_variation.dto import (
    FrozenVariation,
    FrozenVariationPool,
    MessageVariationResult,
)
from core_engine.services.message_variation.errors import (
    GPT_NOT_CONFIGURED,
    GPT_VARIATION_INVALID,
    GptVariationError,
)
from core_engine.services.message_variation.openai_provider import OpenAIMessageVariationProvider
from core_engine.services.message_variation.placeholders import (
    canonical_placeholder_set,
    extract_placeholder_names,
    template_fingerprint,
)
from core_engine.services.message_variation.prompt import SYSTEM_INSTRUCTION_FA, build_user_prompt
from core_engine.services.message_variation.provider import MessageVariationProvider
from core_engine.services.message_variation.validator import validate_variation_set
from core_engine.services.openai_client import is_openai_api_key_configured

logger = logging.getLogger("core_engine.services.message_variation")

MIN_VARIATIONS = 3
MAX_VARIATIONS = 8
DEFAULT_PREVIEW_COUNT = 3

_provider_override: MessageVariationProvider | None = None
_preview_last_monotonic: float = 0.0
_PREVIEW_MIN_INTERVAL_SECONDS = 2.0


def reset_preview_rate_guard() -> None:
    global _preview_last_monotonic
    _preview_last_monotonic = 0.0


def enforce_preview_rate_guard() -> None:
    _preview_rate_guard()


def set_message_variation_provider(provider: MessageVariationProvider | None) -> None:
    global _provider_override
    _provider_override = provider


def get_message_variation_provider() -> MessageVariationProvider:
    if _provider_override is not None:
        return _provider_override
    return OpenAIMessageVariationProvider()


def gpt_status() -> dict[str, Any]:
    settings = get_settings()
    configured = is_openai_api_key_configured(settings) and bool(
        (settings.OPENAI_MODEL or "").strip()
    )
    live = "UNVERIFIED" if configured else "CONFIG_PENDING"
    return {
        "ok": configured,
        "configured": configured,
        "live_binding": live,
        "message": "GPT آماده است" if configured else "GPT تنظیم نشده",
    }


def _requested_count(requested: int | None) -> int:
    settings = get_settings()
    default = int(getattr(settings, "OPENAI_VARIATION_COUNT", 5) or 5)
    value = default if requested is None else int(requested)
    return max(MIN_VARIATIONS, min(MAX_VARIATIONS, value))


def _limits() -> tuple[int, int, int]:
    settings = get_settings()
    min_chars = int(getattr(settings, "OPENAI_VARIATION_MIN_CHARS", 12) or 12)
    max_chars = int(getattr(settings, "OPENAI_VARIATION_MAX_CHARS", 800) or 800)
    attempts = int(getattr(settings, "OPENAI_VARIATION_MAX_ATTEMPTS", 2) or 2)
    return min_chars, max_chars, max(1, min(3, attempts))


def generate_validated_pool(
    template_text: str,
    *,
    requested_count: int | None = None,
    language: str = "fa",
    provider: MessageVariationProvider | None = None,
) -> FrozenVariationPool:
    template = (template_text or "").strip()
    if not template:
        raise GptVariationError(GPT_VARIATION_INVALID, details={"reasons": ["empty_template"]})
    active = provider or get_message_variation_provider()
    if isinstance(active, OpenAIMessageVariationProvider) and _provider_override is None:
        if not is_openai_api_key_configured() or not (get_settings().OPENAI_MODEL or "").strip():
            raise GptVariationError(GPT_NOT_CONFIGURED)

    count = _requested_count(requested_count)
    expected = canonical_placeholder_set(template)
    placeholders = extract_placeholder_names(template)
    min_chars, max_chars, max_attempts = _limits()
    instructions = SYSTEM_INSTRUCTION_FA
    user_input = build_user_prompt(template_text=template, requested_count=count)
    last_error: GptVariationError | None = None
    result: MessageVariationResult | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = active.generate_variations(
                template_text=user_input,
                protected_placeholders=placeholders,
                requested_count=count,
                language=language,
                instructions=instructions,
            )
            texts = [item.text for item in result.variations]
            validate_variation_set(
                texts,
                expected_placeholders=expected,
                min_count=min(count, MIN_VARIATIONS) if count >= MIN_VARIATIONS else count,
                min_chars=min_chars,
                max_chars=max_chars,
            )
            last_error = None
            break
        except GptVariationError as exc:
            last_error = exc
            logger.warning(
                "event=gpt_variation_validation_failed code=%s attempt=%s",
                exc.code,
                attempt,
            )
            result = None
            continue

    if last_error is not None or result is None:
        raise last_error or GptVariationError(GPT_VARIATION_INVALID)

    batch_id = uuid.uuid4().hex
    generated_at = result.generated_at.isoformat()
    frozen = tuple(
        FrozenVariation(
            variation_id=f"{batch_id}:{index}",
            text=item.text.strip(),
            label=item.label or f"v{index}",
            provider=result.provider,
            model=result.model,
            generated_at=generated_at,
            generation_batch_id=batch_id,
        )
        for index, item in enumerate(result.variations, start=1)
    )
    pool = FrozenVariationPool(
        generation_batch_id=batch_id,
        provider=result.provider,
        model=result.model,
        generated_at=generated_at,
        template_fingerprint=template_fingerprint(template),
        variations=frozen,
    )
    logger.info(
        "event=gpt_variation_pool_frozen generation_batch_id=%s variation_count=%s provider=%s model=%s",
        pool.generation_batch_id,
        len(pool.variations),
        pool.provider,
        pool.model,
    )
    return pool


def pool_from_queue_payload(payload: dict[str, Any] | None) -> FrozenVariationPool | None:
    if not payload:
        return None
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
    if not isinstance(meta, dict):
        return None
    raw = meta.get("gpt_variation_pool")
    if not isinstance(raw, dict):
        return None
    pool = FrozenVariationPool.from_dict(raw)
    if not pool.variations or not pool.generation_batch_id:
        return None
    return pool


def assignment_metadata(
    pool: FrozenVariationPool,
    variation: FrozenVariation,
) -> dict[str, Any]:
    return {
        "use_gpt": True,
        "gpt_variation": {
            "generation_batch_id": pool.generation_batch_id,
            "variation_id": variation.variation_id,
            "provider": pool.provider,
            "model": pool.model,
            "generated_at": pool.generated_at,
            "template_fingerprint": pool.template_fingerprint,
        },
        "gpt_variation_pool": pool.to_dict(),
    }


def assign_and_log(
    pool: FrozenVariationPool,
    *,
    campaign_id: int,
    contact_id: int,
) -> FrozenVariation:
    variation = assign_variation(pool, campaign_id=campaign_id, contact_id=contact_id)
    logger.info(
        "event=gpt_variation_assigned campaign_id=%s generation_batch_id=%s variation_id=%s",
        campaign_id,
        pool.generation_batch_id,
        variation.variation_id,
    )
    return variation


def _preview_rate_guard() -> None:
    global _preview_last_monotonic
    now = time.monotonic()
    if now - _preview_last_monotonic < _PREVIEW_MIN_INTERVAL_SECONDS:
        from core_engine.services.message_variation.errors import GPT_RATE_LIMITED

        raise GptVariationError(GPT_RATE_LIMITED)
    _preview_last_monotonic = now


def build_gpt_preview(
    *,
    template_text: str,
    include_products: bool = False,
    requested_count: int | None = None,
    apply_rate_guard: bool = True,
) -> dict[str, Any]:
    if apply_rate_guard:
        _preview_rate_guard()
    preview_count = DEFAULT_PREVIEW_COUNT if requested_count is None else requested_count
    preview_count = max(1, min(DEFAULT_PREVIEW_COUNT, int(preview_count)))
    pool = generate_validated_pool(
        template_text,
        requested_count=max(MIN_VARIATIONS, preview_count),
    )
    samples = []
    product_note = None
    product_error = None
    if include_products:
        from core_engine.services.product_feed.composition import compose_with_locked_products
        from core_engine.services.product_feed.errors import ProductFeedError
        from core_engine.services.product_feed.service import (
            fetch_current_advertising_products,
            select_and_compose,
        )

        try:
            feed = fetch_current_advertising_products()
            composition = select_and_compose(pool.variations[0].text, feed)
            snapshot = composition.snapshot
            if snapshot is None:
                raise ProductFeedError("PRODUCT_FEED_EMPTY")
            product_note = (
                "نمونه پیش‌نمایش؛ انتخاب نهایی محصولات هنگام آماده‌سازی پیام ثبت می‌شود."
            )
            for item in pool.variations[:preview_count]:
                composed = compose_with_locked_products(item.text, snapshot)
                samples.append(
                    {
                        "variation_id": item.variation_id,
                        "label": item.label,
                        "prose_text": composed.prose_text,
                        "immutable_product_block": composed.immutable_product_block,
                        "final_text": composed.final_text,
                        "heading": composed.heading,
                    }
                )
        except ProductFeedError as exc:
            product_error = exc.http_detail()
    if not samples:
        for item in pool.variations[:preview_count]:
            samples.append(
                {
                    "variation_id": item.variation_id,
                    "label": item.label,
                    "prose_text": item.text,
                    "immutable_product_block": "",
                    "final_text": item.text,
                    "heading": "",
                }
            )
    logger.info(
        "event=gpt_preview_generated generation_batch_id=%s variation_count=%s include_products=%s",
        pool.generation_batch_id,
        len(samples),
        include_products,
    )
    status = gpt_status()
    return {
        "ok": product_error is None,
        "code": "OK" if product_error is None else product_error.get("code"),
        "configured": status["configured"],
        "live_binding": status["live_binding"],
        "provider": pool.provider,
        "model": pool.model,
        "generation_batch_id": pool.generation_batch_id,
        "product_preview_note": product_note,
        "product_error": product_error,
        "samples": samples,
        "message": (
            product_error.get("message")
            if product_error
            else "پیش‌نمایش متن‌های پیشنهادی GPT"
        ),
    }
