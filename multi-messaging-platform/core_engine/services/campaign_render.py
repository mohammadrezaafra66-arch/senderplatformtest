"""Canonical campaign final-render pipeline.

Preview (sample) and prepare (commit) must call the same composition function.
The worker must never reconstruct text from the template.
"""

from __future__ import annotations

import hashlib
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core_engine.models import Contact
from core_engine.services.message_variation.assignment import assign_variation
from core_engine.services.message_variation.dto import FrozenVariation, FrozenVariationPool
from core_engine.services.message_variation.placeholders import TEMPLATE_PLACEHOLDER
from core_engine.services.message_variation.service import (
    assignment_metadata,
    generate_validated_pool,
)
from core_engine.services.phase4_utils import build_full_name
from core_engine.services.product_feed.composition import compose_with_locked_products
from core_engine.services.product_feed.dto import FrozenProductSnapshot, MessageComposition
from core_engine.services.product_feed.errors import ProductFeedError
from core_engine.services.product_feed.service import (
    fetch_current_advertising_products,
    select_and_compose,
)

logger = logging.getLogger("core_engine.services.campaign_render")

RENDER_VERSION = "campaign-render-v1"
RENDER_CONTENT_MISMATCH = "RENDER_CONTENT_MISMATCH"
LIST_EXCERPT_MAX_CHARS = 180
DEFAULT_PREVIEW_COUNT = 3
MAX_PREVIEW_COUNT = 5
PREVIEW_SAMPLE_FIRST_NAME = "نمونه"

DEFAULT_PREVIEW_VARIABLES: dict[str, str] = {
    "first_name": PREVIEW_SAMPLE_FIRST_NAME,
    "last_name": "",
    "full_name": PREVIEW_SAMPLE_FIRST_NAME,
    "name": PREVIEW_SAMPLE_FIRST_NAME,
}

ALLOWED_PREVIEW_VARIABLES = frozenset(DEFAULT_PREVIEW_VARIABLES)


class RenderContentMismatchError(Exception):
    """Persisted final_text does not match its frozen SHA-256 (or Message copy)."""

    def __init__(self, code: str = RENDER_CONTENT_MISMATCH) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FinalRenderResult:
    final_text: str
    final_text_sha256: str
    render_version: str
    render_batch_id: str
    campaign_id: int | None
    contact_id: int | None
    rendered_message_id: int | None
    sender_account_id: int | None
    template_source: str
    use_gpt: bool
    generation_batch_id: str | None
    variation_id: str | None
    include_products: bool
    frozen_product_snapshot: dict[str, Any] | None
    product_heading: str | None
    rendered_at: datetime
    gpt_metadata: dict[str, Any] | None
    product_source: str | None
    sample: bool
    unresolved_placeholders: tuple[str, ...] = ()
    composition: MessageComposition | None = field(default=None, hash=False, compare=False)


def new_render_batch_id() -> str:
    return uuid.uuid4().hex


def hash_final_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def excerpt_final_text(
    text: str | None,
    *,
    max_chars: int = LIST_EXCERPT_MAX_CHARS,
) -> tuple[str, bool]:
    """Unicode-code-point excerpt. Does not mutate stored text."""
    if not text:
        return "", False
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def contact_template_variables(contact: Contact) -> dict[str, str]:
    full_name = (
        contact.full_name
        or build_full_name(contact.first_name, contact.last_name)
        or ""
    )
    first = (contact.first_name or "").strip()
    return {
        "first_name": first,
        "last_name": (contact.last_name or "").strip(),
        "full_name": full_name.strip(),
        "name": first,
    }


def substitute_placeholders(template: str, variables: dict[str, str]) -> str:
    """Replace known `{{name}}` tokens. Unknown placeholders stay visible."""

    def _replace(match) -> str:
        name = match.group(1)
        if name in variables:
            return variables[name]
        return match.group(0)

    return TEMPLATE_PLACEHOLDER.sub(_replace, template or "").strip()


def remaining_placeholders(text: str) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for name in TEMPLATE_PLACEHOLDER.findall(text or ""):
        if name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def verify_render_content(
    *,
    final_text: str,
    metadata: dict[str, Any] | None,
    message_rendered_text: str | None = None,
) -> None:
    """Fail closed when a stored hash or Message copy disagrees with payload text.

    Missing hash (pre-5.7 rows) is not a mismatch. Comparison is exact UTF-8
    equality of the canonical stored string — no strip/normalize.
    """
    stored_hash = None
    if isinstance(metadata, dict):
        stored_hash = metadata.get("final_text_sha256")
    if stored_hash:
        computed = hash_final_text(final_text)
        if computed != stored_hash:
            logger.error(
                "event=render_content_mismatch field=hash render_batch_id=%s",
                (metadata or {}).get("render_batch_id"),
            )
            raise RenderContentMismatchError()
    if message_rendered_text is not None and message_rendered_text != final_text:
        logger.error("event=render_content_mismatch field=message_rendered_text")
        raise RenderContentMismatchError()


def _product_fields(composition: MessageComposition | None) -> dict[str, Any]:
    if composition is None or composition.snapshot is None:
        return {
            "frozen_product_snapshot": None,
            "product_heading": None,
            "product_source": None,
            "prose_text": None,
            "immutable_product_block": None,
        }
    return {
        "frozen_product_snapshot": composition.snapshot.to_dict(),
        "product_heading": composition.heading,
        "product_source": composition.snapshot.source,
        "prose_text": composition.prose_text,
        "immutable_product_block": composition.immutable_product_block,
    }


def build_persisted_render_metadata(result: FinalRenderResult) -> dict[str, Any]:
    """Metadata frozen onto queue_payload. Never includes secrets or raw GPT JSON."""
    meta: dict[str, Any] = {
        "render_batch_id": result.render_batch_id,
        "render_version": result.render_version,
        "final_text_sha256": result.final_text_sha256,
        "template_source": result.template_source,
        "use_gpt": result.use_gpt,
        "include_products": result.include_products,
    }
    if result.gpt_metadata:
        meta.update(result.gpt_metadata)
    product = _product_fields(result.composition)
    if product.get("frozen_product_snapshot") is not None:
        meta["frozen_product_snapshot"] = product["frozen_product_snapshot"]
        meta["prose_text"] = product["prose_text"]
        meta["immutable_product_block"] = product["immutable_product_block"]
        meta["product_heading"] = product["product_heading"]
    return meta


def compose_final_render(
    *,
    prose: str,
    render_batch_id: str,
    template_source: str,
    use_gpt: bool,
    include_products: bool,
    contact: Contact | None = None,
    extra_variables: dict[str, str] | None = None,
    substitute: bool = True,
    assigned_variation: FrozenVariation | None = None,
    gpt_pool: FrozenVariationPool | None = None,
    product_feed: Any | None = None,
    product_rng: random.Random | None = None,
    frozen_snapshot: FrozenProductSnapshot | None = None,
    campaign_id: int | None = None,
    contact_id: int | None = None,
    sender_account_id: int | None = None,
    rendered_message_id: int | None = None,
    sample: bool = False,
) -> FinalRenderResult:
    """Single composition path for sample preview and committed prepare."""
    variables: dict[str, str] = {}
    if contact is not None:
        variables.update(contact_template_variables(contact))
    if extra_variables:
        variables.update(extra_variables)

    if substitute:
        rendered_prose = substitute_placeholders(prose, variables)
    else:
        rendered_prose = (prose or "").strip()

    composition: MessageComposition | None = None
    final_text = rendered_prose
    if include_products:
        if frozen_snapshot is not None:
            composition = compose_with_locked_products(rendered_prose, frozen_snapshot)
        elif product_feed is not None:
            composition = select_and_compose(
                rendered_prose, product_feed, rng=product_rng
            )
        else:
            raise ProductFeedError("PRODUCT_FEED_UNAVAILABLE")
        final_text = composition.final_text

    product = _product_fields(composition)
    gpt_meta = None
    generation_batch_id = None
    variation_id = None
    if use_gpt and gpt_pool is not None and assigned_variation is not None:
        gpt_meta = assignment_metadata(gpt_pool, assigned_variation)
        generation_batch_id = assigned_variation.generation_batch_id
        variation_id = assigned_variation.variation_id

    return FinalRenderResult(
        final_text=final_text,
        final_text_sha256=hash_final_text(final_text),
        render_version=RENDER_VERSION,
        render_batch_id=render_batch_id,
        campaign_id=campaign_id,
        contact_id=contact_id,
        rendered_message_id=rendered_message_id,
        sender_account_id=sender_account_id,
        template_source=template_source,
        use_gpt=use_gpt,
        generation_batch_id=generation_batch_id,
        variation_id=variation_id,
        include_products=include_products,
        frozen_product_snapshot=product["frozen_product_snapshot"],
        product_heading=product["product_heading"],
        rendered_at=datetime.now(timezone.utc),
        gpt_metadata=gpt_meta,
        product_source=product["product_source"],
        sample=sample,
        unresolved_placeholders=remaining_placeholders(rendered_prose),
        composition=composition,
    )


def _sanitize_preview_variables(
    supplied: dict[str, str] | None,
) -> dict[str, str]:
    merged = dict(DEFAULT_PREVIEW_VARIABLES)
    if not supplied:
        return merged
    for key, value in supplied.items():
        if key in ALLOWED_PREVIEW_VARIABLES and isinstance(value, str):
            merged[key] = value
    if not (merged.get("full_name") or "").strip():
        merged["full_name"] = (merged.get("first_name") or PREVIEW_SAMPLE_FIRST_NAME).strip()
    if not (merged.get("name") or "").strip():
        merged["name"] = (merged.get("first_name") or PREVIEW_SAMPLE_FIRST_NAME).strip()
    return merged


def build_campaign_render_preview(
    *,
    template_text: str,
    use_gpt: bool = False,
    include_products: bool = False,
    preview_count: int | None = None,
    preview_variables: dict[str, str] | None = None,
    apply_gpt_rate_guard: bool = True,
) -> dict[str, Any]:
    """Non-committed sample preview using the same composer as prepare."""
    template = (template_text or "").strip()
    if not template:
        raise ValueError("template_text is required")

    count = DEFAULT_PREVIEW_COUNT if preview_count is None else int(preview_count)
    count = max(1, min(MAX_PREVIEW_COUNT, count))
    variables = _sanitize_preview_variables(preview_variables)
    render_batch_id = new_render_batch_id()

    gpt_pool: FrozenVariationPool | None = None
    if use_gpt:
        if apply_gpt_rate_guard:
            from core_engine.services.message_variation.service import (
                enforce_preview_rate_guard,
            )

            enforce_preview_rate_guard()
        gpt_pool = generate_validated_pool(template)

    product_feed = None
    product_error = None
    if include_products:
        try:
            product_feed = fetch_current_advertising_products()
        except ProductFeedError as exc:
            product_error = exc.http_detail()

    samples: list[dict[str, Any]] = []
    for index in range(count):
        assigned: FrozenVariation | None = None
        template_source = "base_template"
        prose = template
        if use_gpt and gpt_pool is not None:
            assigned = assign_variation(
                gpt_pool, campaign_id=0, contact_id=index + 1
            )
            prose = assigned.text
            template_source = "gpt_variation"
        rng = (
            random.Random(f"preview:{render_batch_id}:{index}")
            if include_products and product_feed is not None
            else None
        )
        result = compose_final_render(
            prose=prose,
            render_batch_id=render_batch_id,
            template_source=template_source,
            use_gpt=bool(use_gpt and gpt_pool is not None),
            include_products=bool(include_products and product_feed is not None),
            extra_variables=variables,
            substitute=True,
            assigned_variation=assigned,
            gpt_pool=gpt_pool,
            product_feed=product_feed,
            product_rng=rng,
            sample=True,
        )
        composition = result.composition
        samples.append(
            {
                "committed": False,
                "preview_kind": "sample",
                "label": "پیش‌نمایش نمونه",
                "final_text": result.final_text,
                "final_text_sha256": result.final_text_sha256,
                "render_version": result.render_version,
                "render_batch_id": result.render_batch_id,
                "template_source": result.template_source,
                "use_gpt": result.use_gpt,
                "generation_batch_id": result.generation_batch_id,
                "variation_id": result.variation_id,
                "variation_label": assigned.label if assigned is not None else None,
                "include_products": result.include_products,
                "product_count": (
                    len(result.frozen_product_snapshot.get("products") or [])
                    if result.frozen_product_snapshot
                    else 0
                ),
                "product_heading": result.product_heading,
                "product_source": result.product_source,
                "product_fetched_at": (
                    result.frozen_product_snapshot.get("fetched_at")
                    if result.frozen_product_snapshot
                    else None
                ),
                "immutable_product_block": (
                    composition.immutable_product_block if composition is not None else ""
                ),
                "prose_text": composition.prose_text if composition is not None else result.final_text,
                "unresolved_placeholders": list(result.unresolved_placeholders),
                "sample_warning": "این متن پیش‌نمایش نمونه است و پیام نهایی ثبت‌شده نیست.",
            }
        )

    required = remaining_placeholders(template)
    missing_preview_vars = [
        name for name in required if name not in ALLOWED_PREVIEW_VARIABLES
    ]
    return {
        "ok": product_error is None and (not use_gpt or gpt_pool is not None),
        "committed": False,
        "preview_kind": "sample",
        "label": "پیش‌نمایش نمونه",
        "render_version": RENDER_VERSION,
        "render_batch_id": render_batch_id,
        "use_gpt": use_gpt,
        "include_products": include_products,
        "preview_count": len(samples),
        "preview_variables": variables,
        "preview_variables_required": missing_preview_vars,
        "generation_batch_id": gpt_pool.generation_batch_id if gpt_pool else None,
        "provider": gpt_pool.provider if gpt_pool else None,
        "model": gpt_pool.model if gpt_pool else None,
        "product_error": product_error,
        "samples": samples,
        "message": (
            product_error.get("message")
            if product_error
            else "پیش‌نمایش نمونه — این متن‌ها هنوز برای ارسال ثبت نشده‌اند."
        ),
    }


def public_gpt_trace(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    gpt = metadata.get("gpt_variation")
    if not isinstance(gpt, dict):
        if metadata.get("use_gpt"):
            return {
                "use_gpt": True,
                "provider": None,
                "model": None,
                "generation_batch_id": None,
                "variation_id": None,
            }
        return None
    return {
        "use_gpt": True,
        "provider": gpt.get("provider"),
        "model": gpt.get("model"),
        "generation_batch_id": gpt.get("generation_batch_id"),
        "variation_id": gpt.get("variation_id"),
        "generated_at": gpt.get("generated_at"),
    }


def public_product_trace(
    metadata: dict[str, Any] | None,
    *,
    include_snapshot: bool,
) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    snapshot = metadata.get("frozen_product_snapshot")
    if not isinstance(snapshot, dict):
        if metadata.get("include_products"):
            return {"include_products": True, "product_count": 0, "products": []}
        return None
    products = snapshot.get("products") or []
    summary: dict[str, Any] = {
        "include_products": True,
        "product_count": len(products),
        "heading": snapshot.get("heading"),
        "source": snapshot.get("source"),
        "provider": snapshot.get("provider"),
        "fetched_at": snapshot.get("fetched_at"),
    }
    if include_snapshot:
        summary["products"] = [
            {
                "external_id": item.get("external_id"),
                "product_code": item.get("product_code"),
                "name": item.get("name"),
                "price": item.get("price"),
                "currency": item.get("currency"),
                "display_price": item.get("display_price"),
                "source_updated_at": item.get("source_updated_at"),
                "fetched_at": item.get("fetched_at"),
            }
            for item in products
            if isinstance(item, dict)
        ]
    return summary


def list_trace_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    meta = {}
    if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict):
        meta = payload["metadata"]
    gpt = public_gpt_trace(meta) or {}
    product = public_product_trace(meta, include_snapshot=False) or {}
    return {
        "render_batch_id": meta.get("render_batch_id"),
        "render_version": meta.get("render_version"),
        "final_text_sha256": meta.get("final_text_sha256"),
        "use_gpt": bool(gpt.get("use_gpt") or meta.get("use_gpt")),
        "variation_id": gpt.get("variation_id"),
        "include_products": bool(product.get("include_products") or meta.get("include_products")),
        "product_count": product.get("product_count") or 0,
    }
