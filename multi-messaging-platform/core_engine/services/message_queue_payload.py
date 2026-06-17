"""ساخت و ذخیره payload آماده صف از پیام رندرشده.

Phase 4 must use database staging only. This module builds JSON payloads for
persistence on RenderedMessage — it does not push to Redis worker queues.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core_engine.api.schemas import SaveRenderedMessageDryRunRequest
from core_engine.api.utf8_json import encoding_debug_fields
from core_engine.models import RenderedMessage


def _parse_snapshot_expires_at(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _customer_display_name(request: SaveRenderedMessageDryRunRequest) -> str:
    if request.customer_name:
        return request.customer_name.strip()
    if request.last_name:
        return f"{request.first_name} {request.last_name}".strip()
    return request.first_name or ""


def build_queue_payload_from_rendered_message(
    rendered_message: RenderedMessage,
    *,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    intent: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "render_mode": rendered_message.render_mode,
        "used_kb": rendered_message.used_kb,
        "used_products": rendered_message.used_products,
        "ready_for_queue": rendered_message.ready_for_queue,
    }

    if customer_name:
        metadata["customer_name"] = customer_name
    if customer_phone:
        metadata["customer_phone"] = customer_phone
    if intent:
        metadata["intent"] = intent

    if rendered_message.used_products and rendered_message.product_snapshot_id is not None:
        metadata["product_snapshot_id"] = rendered_message.product_snapshot_id
    else:
        metadata["product_snapshot_id"] = None

    if rendered_message.snapshot_expires_at is not None:
        metadata["snapshot_expires_at"] = rendered_message.snapshot_expires_at.isoformat()
    else:
        metadata["snapshot_expires_at"] = None

    if rendered_message.warnings:
        metadata["warnings"] = rendered_message.warnings

    if (
        rendered_message.product_snapshot_id is not None
        and not rendered_message.used_products
    ):
        metadata["trace_snapshot_id"] = rendered_message.product_snapshot_id

    return {
        "rendered_message_id": rendered_message.id,
        "campaign_id": rendered_message.campaign_id,
        "contact_id": rendered_message.contact_id,
        "channel": rendered_message.channel,
        "final_text": rendered_message.final_text,
        "metadata": metadata,
    }


def save_rendered_message(
    db: Session,
    request: SaveRenderedMessageDryRunRequest,
    render_result: dict[str, Any],
    *,
    campaign_id: int | None = None,
    contact_id: int | None = None,
) -> dict[str, Any]:
    if not render_result.get("success"):
        return render_result

    resolved_campaign_id = (
        campaign_id if campaign_id is not None else request.campaign_id
    )
    resolved_contact_id = contact_id if contact_id is not None else request.contact_id

    message_data = render_result.get("message") or {}
    final_text = (message_data.get("final_text") or render_result.get("final_text") or "").strip()
    warnings = list(render_result.get("warnings") or message_data.get("warnings") or [])
    ready_for_queue = bool(render_result.get("ready_for_queue"))
    used_products = bool(render_result.get("used_products"))
    snapshot_id = render_result.get("snapshot_id")

    customer_name = _customer_display_name(request)

    rendered_message = RenderedMessage(
        campaign_id=resolved_campaign_id,
        contact_id=resolved_contact_id,
        channel=request.channel,
        final_text=final_text,
        render_mode=str(render_result.get("mode") or "unknown"),
        used_kb=bool(render_result.get("used_kb")),
        used_products=used_products,
        product_snapshot_id=snapshot_id,
        snapshot_expires_at=_parse_snapshot_expires_at(
            render_result.get("snapshot_expires_at")
        ),
        ready_for_queue=ready_for_queue,
        warnings=warnings or None,
    )
    db.add(rendered_message)
    db.flush()

    queue_payload = build_queue_payload_from_rendered_message(
        rendered_message,
        customer_name=customer_name,
        customer_phone=request.customer_phone,
        intent=request.intent,
    )
    rendered_message.queue_payload = queue_payload
    db.commit()
    db.refresh(rendered_message)

    return {
        "success": True,
        "rendered_message_id": rendered_message.id,
        "ready_for_queue": rendered_message.ready_for_queue,
        "products_count": render_result.get("products_count", 0),
        "customer_name": customer_name,
        "final_text": final_text,
        "queue_payload": queue_payload,
        "warnings": warnings,
        "encoding_debug": {
            "customer_name": encoding_debug_fields(customer_name),
            "final_text": encoding_debug_fields(final_text),
            "queue_payload_final_text": encoding_debug_fields(
                queue_payload.get("final_text")
            ),
        },
    }


def save_dry_run_rendered_message(
    db: Session,
    request: SaveRenderedMessageDryRunRequest,
) -> dict[str, Any]:
    from core_engine.services.message_render import dry_run_message_render

    render_result = dry_run_message_render(db, request.to_generate_request())
    return save_rendered_message(db, request, render_result)


def list_latest_rendered_messages(
    db: Session,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = (
        db.query(RenderedMessage)
        .order_by(RenderedMessage.created_at.desc(), RenderedMessage.id.desc())
        .limit(limit)
        .all()
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        metadata = (row.queue_payload or {}).get("metadata") or {}
        item: dict[str, Any] = {
            "id": row.id,
            "customer_name": metadata.get("customer_name"),
            "campaign_id": row.campaign_id,
            "contact_id": row.contact_id,
            "channel": row.channel,
            "final_text": row.final_text,
            "render_mode": row.render_mode,
            "used_products": row.used_products,
            "product_snapshot_id": row.product_snapshot_id,
            "ready_for_queue": row.ready_for_queue,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "encoding_debug": {
                "customer_name": encoding_debug_fields(metadata.get("customer_name")),
                "final_text": encoding_debug_fields(row.final_text),
            },
        }
        if row.queue_payload:
            item["queue_payload"] = row.queue_payload
            item["queue_payload_summary"] = {
                "rendered_message_id": row.queue_payload.get("rendered_message_id"),
                "channel": row.queue_payload.get("channel"),
                "ready_for_queue": metadata.get("ready_for_queue"),
                "customer_phone": metadata.get("customer_phone"),
                "intent": metadata.get("intent"),
            }
        results.append(item)
    return results
