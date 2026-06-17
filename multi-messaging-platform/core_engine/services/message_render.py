"""تولید و اعتبارسنجی پیام نهایی برای dry-run بدون ارسال."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core_engine.api.schemas import GenerateMessageRequest, PersonalizedMessageOutput
from core_engine.api.utf8_json import encoding_debug_fields
from core_engine.services.gpt_orchestrator import (
    build_product_context,
    generate_personalized_message,
    prepare_gpt_generation_context,
)
from core_engine.services.product_snapshot import get_latest_valid_product_snapshot

MAX_FINAL_TEXT_CHARS = 450
DEFAULT_CTA = "اگر موجودی و شرایط خرید را می‌خواهید، پیام بدهید."
DEFAULT_BODY_WITH_PRODUCTS = (
    "چند مدل پرفروش امروز افراکالا برای همکاری B2B آماده بررسی است"
)
DEFAULT_BODY_WITHOUT_PRODUCTS = "افراکالا برای همکاری B2B آماده هماهنگی است"


def _compact_product_label(product: dict[str, Any]) -> str:
    title = (product.get("title") or "").strip()
    brand = (product.get("brand") or "").strip()
    model = (product.get("model") or "").strip()

    if "ظرفشویی" in title and brand and model:
        return f"ظرفشویی {brand} {model}"
    if "تلویزیون" in title and brand and model:
        return f"تلویزیون {brand} {model}"
    if title:
        return title
    if brand and model:
        return f"{brand} {model}"
    return brand or model or "محصول"


def build_mock_personalized_message(
    request: GenerateMessageRequest,
    product_context: dict[str, Any],
) -> PersonalizedMessageOutput:
    warnings: list[str] = []
    product_warning = product_context.get("warning")
    if product_warning:
        warnings.append(product_warning)

    display_name = request.last_name or request.first_name
    greeting = f"سلام آقای {display_name}"
    cta = DEFAULT_CTA
    product_block: str | None = None

    if product_context.get("enabled") and product_context.get("products"):
        body = DEFAULT_BODY_WITH_PRODUCTS
        labels = [_compact_product_label(product) for product in product_context["products"]]
        product_block = "، ".join(labels)
        final_text = f"{greeting}، {body} {product_block}. {cta}"
    else:
        body = DEFAULT_BODY_WITHOUT_PRODUCTS
        final_text = f"{greeting}، {body}. {cta}"

    return PersonalizedMessageOutput(
        greeting=greeting,
        body=body,
        cta=cta,
        product_block=product_block,
        final_text=final_text,
        warnings=warnings,
    )


def validate_rendered_message(
    message: dict[str, Any] | PersonalizedMessageOutput,
    *,
    used_products: bool,
) -> dict[str, Any]:
    if isinstance(message, PersonalizedMessageOutput):
        payload = message.model_dump()
    else:
        payload = message

    warnings = list(payload.get("warnings") or [])
    final_text = (payload.get("final_text") or "").strip()
    ready_for_queue = True

    if not final_text:
        warnings.append("final_text is empty")
        ready_for_queue = False
    elif len(final_text) > MAX_FINAL_TEXT_CHARS:
        warnings.append(
            f"final_text exceeds {MAX_FINAL_TEXT_CHARS} characters ({len(final_text)})"
        )
        ready_for_queue = False

    cta = (payload.get("cta") or "").strip()
    if cta and final_text.count(cta) > 1:
        warnings.append("Multiple CTA phrases detected; only one CTA is recommended.")

    product_block = payload.get("product_block")
    if product_block and not used_products:
        warnings.append(
            "product_block is present but no valid product snapshot was used."
        )

    deduped_warnings: list[str] = []
    for warning in warnings:
        if warning not in deduped_warnings:
            deduped_warnings.append(warning)

    return {
        "ready_for_queue": ready_for_queue,
        "warnings": deduped_warnings,
    }


def _build_success_response(
    *,
    mode: str,
    message: PersonalizedMessageOutput | dict[str, Any],
    used_kb: bool,
    used_products: bool,
    snapshot_id: int | None,
    snapshot_expires_at: str | None,
    products_count: int,
    snapshot_warning: str | None = None,
) -> dict[str, Any]:
    if isinstance(message, PersonalizedMessageOutput):
        message_payload = message.model_dump()
    else:
        message_payload = dict(message)

    validation = validate_rendered_message(
        message_payload,
        used_products=used_products,
    )
    merged_warnings = list(message_payload.get("warnings") or [])
    if snapshot_warning and snapshot_warning not in merged_warnings:
        merged_warnings.append(snapshot_warning)
    for warning in validation["warnings"]:
        if warning not in merged_warnings:
            merged_warnings.append(warning)
    message_payload["warnings"] = merged_warnings

    final_text = (message_payload.get("final_text") or "").strip()
    return {
        "success": True,
        "mode": mode,
        "used_kb": used_kb,
        "used_products": used_products,
        "products_count": products_count,
        "snapshot_id": snapshot_id,
        "snapshot_expires_at": snapshot_expires_at,
        "message": message_payload,
        "final_text": final_text,
        "final_text_length": len(final_text),
        "ready_for_queue": validation["ready_for_queue"],
        "warnings": merged_warnings,
        "encoding_debug": {
            "final_text": encoding_debug_fields(final_text),
            "greeting": encoding_debug_fields(message_payload.get("greeting")),
            "product_block": encoding_debug_fields(message_payload.get("product_block")),
        },
    }


def dry_run_message_render(
    db: Session,
    request: GenerateMessageRequest,
) -> dict[str, Any]:
    snapshot_meta = get_latest_valid_product_snapshot(db)
    snapshot_warning: str | None = None
    if request.include_products and not snapshot_meta.get("found"):
        snapshot_warning = str(
            snapshot_meta.get("reason")
            or "No valid product pricing snapshot available"
        )
        if snapshot_meta.get("reason") == "Latest product snapshot is expired":
            snapshot_warning = "Latest product snapshot is expired"

    context = prepare_gpt_generation_context(db, request)
    if not context.get("success"):
        return context

    used_kb = bool(context["used_kb"])
    used_products = bool(context["used_products"])
    snapshot_id = context.get("snapshot_id")
    snapshot_expires_at = context.get("snapshot_expires_at")
    products = context.get("product_context", {}).get("products") or []
    products_count = len(products) if used_products else 0

    if request.force_mock_output:
        message = build_mock_personalized_message(
            request,
            context["product_context"],
        )
        return _build_success_response(
            mode="mock",
            message=message,
            used_kb=used_kb,
            used_products=used_products,
            snapshot_id=snapshot_id,
            snapshot_expires_at=snapshot_expires_at,
            products_count=products_count,
            snapshot_warning=snapshot_warning,
        )

    generation = generate_personalized_message(db, request)
    if not generation.get("success"):
        return generation

    message = generation["message"]
    gen_products = generation.get("used_products", used_products)
    if gen_products:
        product_context = build_product_context(
            db,
            include_products=True,
            max_products=request.max_products,
        )
        products_count = len(product_context.get("products") or [])

    return _build_success_response(
        mode="openai",
        message=message,
        used_kb=generation.get("used_kb", used_kb),
        used_products=generation.get("used_products", used_products),
        snapshot_id=generation.get("snapshot_id", snapshot_id),
        snapshot_expires_at=generation.get("snapshot_expires_at", snapshot_expires_at),
        products_count=products_count,
        snapshot_warning=snapshot_warning,
    )
