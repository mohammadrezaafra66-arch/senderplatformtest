"""هماهنگ‌سازی با GPT برای تولید پیام فروش شخصی‌سازی‌شده."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from core_engine.api.schemas import GenerateMessageRequest, PersonalizedMessageOutput
from core_engine.config import get_settings
from core_engine.models import ProductSnapshot
from core_engine.services.knowledge_base import build_kb_context
from core_engine.services.openai_client import (
    get_openai_client,
    is_openai_api_key_configured,
)
from core_engine.services.product_snapshot import get_latest_valid_product_snapshot

PRODUCT_FIELDS = ("title", "brand", "model", "cash_price", "availability", "currency")


def build_product_context(
    db: Session,
    include_products: bool,
    max_products: int,
) -> dict[str, Any]:
    if not include_products:
        return {
            "enabled": False,
            "products": [],
            "snapshot_id": None,
            "snapshot_expires_at": None,
            "warning": None,
        }

    snapshot_meta = get_latest_valid_product_snapshot(db)
    if not snapshot_meta.get("found"):
        return {
            "enabled": False,
            "products": [],
            "snapshot_id": snapshot_meta.get("snapshot_id"),
            "snapshot_expires_at": snapshot_meta.get("expired_at")
            or snapshot_meta.get("expires_at"),
            "warning": "No valid product pricing snapshot available",
        }

    snapshot = (
        db.query(ProductSnapshot)
        .filter(ProductSnapshot.id == snapshot_meta["snapshot_id"])
        .first()
    )
    if snapshot is None:
        return {
            "enabled": False,
            "products": [],
            "snapshot_id": snapshot_meta.get("snapshot_id"),
            "snapshot_expires_at": snapshot_meta.get("expires_at"),
            "warning": "No valid product pricing snapshot available",
        }

    normalized_payload = snapshot.normalized_payload or {}
    raw_payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    currency = raw_payload.get("currency")

    items = normalized_payload.get("normalized_items")
    if not isinstance(items, list):
        items = []

    products: list[dict[str, Any]] = []
    for item in items[:max_products]:
        if not isinstance(item, dict):
            continue
        product: dict[str, Any] = {
            key: item.get(key)
            for key in ("title", "brand", "model", "cash_price", "availability")
            if item.get(key) is not None
        }
        if currency:
            product["currency"] = currency
        products.append(product)

    return {
        "enabled": True,
        "products": products,
        "snapshot_id": snapshot.id,
        "snapshot_expires_at": snapshot.expires_at.isoformat()
        if snapshot.expires_at
        else None,
        "warning": None,
    }


def build_gpt_prompt(
    request: GenerateMessageRequest,
    kb_context: dict[str, Any],
    product_context: dict[str, Any],
) -> dict[str, str]:
    customer_name = request.first_name
    if request.last_name:
        customer_name = f"{request.first_name} {request.last_name}"

    system_message = (
        "تو نویسنده پیام فروش B2B برای افراکالا هستی.\n"
        "قوانین:\n"
        "- فارسی، محترمانه، کوتاه و حرفه‌ای بنویس.\n"
        "- ادعای غیرمستند، تخفیف قطعی یا موجودی قطعی بدون داده نکن.\n"
        "- اگر قیمت معتبر در داده محصولات نیست، هیچ عدد قیمتی نگو.\n"
        "- اگر محصول داده شد، فقط از همان داده‌ها استفاده کن.\n"
        "- فقط یک CTA بنویس.\n"
        "- final_text باید زیر ۴۵۰ کاراکتر باشد.\n"
        "- خروجی را دقیقاً مطابق schema درخواستی برگردان."
    )

    user_sections = [
        f"نام مشتری: {customer_name}",
        f"کانال: {request.channel}",
        f"هدف پیام: {request.goal}",
        "",
        "پایگاه دانش:",
        kb_context.get("context", ""),
    ]

    if product_context.get("enabled") and product_context.get("products"):
        user_sections.extend(
            [
                "",
                "محصولات معتبر (فقط از این داده‌ها استفاده کن):",
                json.dumps(product_context["products"], ensure_ascii=False, indent=2),
                f"snapshot_id: {product_context.get('snapshot_id')}",
                f"snapshot_expires_at: {product_context.get('snapshot_expires_at')}",
            ]
        )
    elif product_context.get("warning"):
        user_sections.extend(
            [
                "",
                "هشدار محصولات:",
                product_context["warning"],
                "در این حالت product_block را null بگذار و درباره قیمت عددی صحبت نکن.",
            ]
        )

    return {
        "system": system_message,
        "user": "\n".join(user_sections),
    }


def prepare_gpt_generation_context(
    db: Session,
    request: GenerateMessageRequest,
) -> dict[str, Any]:
    kb_context = build_kb_context(max_chars=request.max_kb_chars)
    if not kb_context.get("success"):
        return {
            "success": False,
            "error": kb_context.get("error", "Failed to build knowledge base context."),
        }

    product_context = build_product_context(
        db,
        include_products=request.include_products,
        max_products=request.max_products,
    )
    prompt = build_gpt_prompt(request, kb_context, product_context)

    return {
        "success": True,
        "kb_context": kb_context,
        "product_context": product_context,
        "prompt": prompt,
        "used_kb": True,
        "used_products": bool(product_context.get("enabled")),
        "snapshot_id": product_context.get("snapshot_id"),
        "snapshot_expires_at": product_context.get("snapshot_expires_at"),
    }


def preview_personalized_message(
    db: Session,
    request: GenerateMessageRequest,
) -> dict[str, Any]:
    context = prepare_gpt_generation_context(db, request)
    if not context.get("success"):
        return context

    return {
        "success": True,
        "kb_context": context["kb_context"],
        "product_context": context["product_context"],
        "prompt": context["prompt"],
        "used_kb": context["used_kb"],
        "used_products": context["used_products"],
        "snapshot_id": context["snapshot_id"],
        "snapshot_expires_at": context["snapshot_expires_at"],
    }


def generate_personalized_message(
    db: Session,
    request: GenerateMessageRequest,
) -> dict[str, Any]:
    context = prepare_gpt_generation_context(db, request)
    if not context.get("success"):
        return context

    if not is_openai_api_key_configured():
        return {
            "success": False,
            "error": "OPENAI_API_KEY is not configured",
        }

    settings = get_settings()
    try:
        client = get_openai_client()
        completion = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": context["prompt"]["system"]},
                {"role": "user", "content": context["prompt"]["user"]},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "personalized_message_output",
                    "schema": PersonalizedMessageOutput.model_json_schema(),
                    "strict": True,
                },
            },
        )
        raw_content = completion.choices[0].message.content
        if not raw_content:
            return {
                "success": False,
                "error": "OpenAI returned an empty response.",
            }

        parsed = PersonalizedMessageOutput.model_validate_json(raw_content)
        warnings = list(parsed.warnings)
        product_warning = context["product_context"].get("warning")
        if product_warning and product_warning not in warnings:
            warnings.append(product_warning)

        message = parsed.model_copy(update={"warnings": warnings})
        if not context["product_context"].get("enabled"):
            message = message.model_copy(update={"product_block": None})

        return {
            "success": True,
            "message": message.model_dump(),
            "used_kb": context["used_kb"],
            "used_products": context["used_products"],
            "snapshot_id": context["snapshot_id"],
            "snapshot_expires_at": context["snapshot_expires_at"],
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"GPT message generation failed: {exc}",
        }
