"""Send-time AfraKala refresh. Prepare identity is not the outbound price.

Selected product ids may be frozen at prepare. Price, availability, and the
exact advertising tag are re-checked from the read-only catalog immediately
before an outbound attempt that has not yet been submitted to the connector.

A shared fetch may be reused only inside one dispatch cycle, and only while
that fetch is younger than the existing AFRAKALA_PRODUCT_MAX_STALENESS_SECONDS
limit. That limit is not a permission to send a prepare-time price. A fetch
older than the limit cannot be reused. The audit snapshot records what this
attempt used. It is not the source of the next outbound price.

Exactly-once boundary:
- Before external_send_submitted, a retry refreshes again and uses the latest
  valid cash/prepayment price.
- After the live connector is about to be called, the payload is marked
  submitted. A later retry must replay that already-rendered text. The
  connector may have accepted the first request without a local success record,
  so a second refresh must not create a different outbound message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core_engine.services.campaign_footer import append_campaign_footer
from core_engine.services.product_feed.advertising_eligibility import (
    CASH_PREPAYMENT_PRICE_MISSING,
    INVALID_PRICE,
    MISSING_ADVERTISING_TAG,
    PRODUCT_UNAVAILABLE,
    evaluate_advertising_product,
)
from core_engine.services.product_feed.composition import compose_campaign_message
from core_engine.services.product_feed.errors import ProductFeedError
from core_engine.services.product_feed.service import _max_staleness_seconds, _unit_label

PRODUCT_UNAVAILABLE_AT_SEND = "PRODUCT_UNAVAILABLE_AT_SEND"
PRODUCT_NOT_ADVERTISING_AT_SEND = "PRODUCT_NOT_ADVERTISING_AT_SEND"
CASH_PREPAYMENT_PRICE_MISSING_AT_SEND = "CASH_PREPAYMENT_PRICE_MISSING_AT_SEND"
PRODUCT_REFRESH_FAILED = "PRODUCT_REFRESH_FAILED"

SEND_TIME_PRICE_NOTICE = "قیمت محصول هنگام ارسال مجدداً از افراکالا بررسی می‌شود."

_REASON_PRIORITY = (
    PRODUCT_REFRESH_FAILED,
    PRODUCT_NOT_ADVERTISING_AT_SEND,
    PRODUCT_UNAVAILABLE_AT_SEND,
    CASH_PREPAYMENT_PRICE_MISSING_AT_SEND,
)


@dataclass
class SendTimeRefreshCycle:
    """One in-process dispatch cycle. Not a long-lived price cache."""

    max_age_seconds: int | None = None
    fetched_at: datetime | None = None
    rows: list[dict[str, Any]] | None = None
    fetch_count: int = 0

    def limit(self) -> int:
        if self.max_age_seconds is not None:
            return int(self.max_age_seconds)
        return _max_staleness_seconds()


@dataclass
class SendTimeRefreshResult:
    blocked: bool
    refreshed: bool
    message_text: str | None
    reason_code: str | None = None
    reason_message: str | None = None
    audit: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False


def external_send_may_have_accepted(payload: dict[str, Any]) -> bool:
    """True after the live connector may already have seen this text."""
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if metadata.get("external_send_submitted") is True:
        return True
    if metadata.get("platform_message_id"):
        return True
    return False


def _now(clock: datetime | None) -> datetime:
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _as_payload(payload: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    raise TypeError("outbound payload must be a dict or pydantic model")


def _selected_ids(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("selected_product_ids")
    if isinstance(raw, list) and raw:
        return [str(item).strip() for item in raw if str(item).strip()]
    snapshot = metadata.get("frozen_product_snapshot")
    if isinstance(snapshot, dict):
        ids = []
        for item in snapshot.get("products") or []:
            if isinstance(item, dict) and item.get("external_id"):
                ids.append(str(item["external_id"]).strip())
        return [item for item in ids if item]
    return []


def _row_ids(row: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ("id", "external_id", "sku", "product_code"):
        value = row.get(key)
        if value is not None and str(value).strip():
            found.add(str(value).strip())
    return found


def _catalog_rows(provider: Any, *, clock: datetime | None) -> list[dict[str, Any]]:
    if hasattr(provider, "fetch_catalog_rows"):
        rows = provider.fetch_catalog_rows(clock=clock)
    else:
        result = provider.fetch_advertising_products(clock=clock)
        rows = []
        for product in result.products:
            rows.append(
                {
                    "id": product.external_id,
                    "sku": product.product_code or product.external_id,
                    "name": product.name,
                    "status": "active",
                    "stock_status": "available",
                    "labels": [{"title": "تبلیغات"}],
                    "cash_price": product.price,
                    "currency": product.currency,
                }
            )
    if not isinstance(rows, list):
        raise ProductFeedError(PRODUCT_REFRESH_FAILED, "پاسخ افراکالا قابل استفاده نیست.")
    return [row for row in rows if isinstance(row, dict)]


def _load_rows(
    provider: Any,
    *,
    cycle: SendTimeRefreshCycle | None,
    clock: datetime | None,
) -> tuple[list[dict[str, Any]], datetime]:
    now = _now(clock)
    if cycle is not None and cycle.rows is not None and cycle.fetched_at is not None:
        age = (now - cycle.fetched_at).total_seconds()
        if age <= cycle.limit():
            return cycle.rows, cycle.fetched_at
    try:
        rows = _catalog_rows(provider, clock=now)
    except ProductFeedError as exc:
        raise ProductFeedError(
            PRODUCT_REFRESH_FAILED,
            exc.message,
            details={"source_code": exc.code},
        ) from exc
    except Exception as exc:
        raise ProductFeedError(PRODUCT_REFRESH_FAILED, "تازه‌سازی افراکالا ناموفق بود.") from exc
    if cycle is not None:
        cycle.rows = rows
        cycle.fetched_at = now
        cycle.fetch_count += 1
    return rows, now


def _send_reason(reason_codes: tuple[str, ...]) -> str:
    mapped = []
    for code in reason_codes:
        if code == MISSING_ADVERTISING_TAG:
            mapped.append(PRODUCT_NOT_ADVERTISING_AT_SEND)
        elif code == PRODUCT_UNAVAILABLE:
            mapped.append(PRODUCT_UNAVAILABLE_AT_SEND)
        elif code in {CASH_PREPAYMENT_PRICE_MISSING, INVALID_PRICE}:
            mapped.append(CASH_PREPAYMENT_PRICE_MISSING_AT_SEND)
        else:
            mapped.append(PRODUCT_REFRESH_FAILED)
    for preferred in _REASON_PRIORITY:
        if preferred in mapped:
            return preferred
    return PRODUCT_REFRESH_FAILED


def _reason_message(code: str) -> str:
    return {
        PRODUCT_UNAVAILABLE_AT_SEND: "محصول هنگام ارسال ناموجود است و پیام ارسال نشد.",
        PRODUCT_NOT_ADVERTISING_AT_SEND: "محصول هنگام ارسال تگ دقیق تبلیغات ندارد و پیام ارسال نشد.",
        CASH_PREPAYMENT_PRICE_MISSING_AT_SEND: "قیمت نقدی (پیش واریز) هنگام ارسال معتبر نیست و پیام ارسال نشد.",
        PRODUCT_REFRESH_FAILED: "تازه‌سازی محصول از افراکالا ناموفق بود و پیام قدیمی ارسال نشد.",
    }.get(code, "پیام محصول به‌دلیل نامعتبر بودن ارسال نشد.")


def render_outbound_from_selection(
    payload: dict[str, Any] | Any,
    *,
    provider: Any,
    cycle: SendTimeRefreshCycle | None = None,
    clock: datetime | None = None,
) -> SendTimeRefreshResult:
    """Refresh selected products and render the text this attempt may send."""
    data = _as_payload(payload)
    metadata = dict(data.get("metadata") or {})
    if external_send_may_have_accepted(data):
        text = str(data.get("message_text") or "")
        return SendTimeRefreshResult(
            blocked=False,
            refreshed=False,
            message_text=text,
            payload=data,
            audit=metadata.get("send_time_product_audit")
            if isinstance(metadata.get("send_time_product_audit"), dict)
            else None,
        )

    selected = _selected_ids(metadata)
    if not selected or not metadata.get("include_products"):
        return SendTimeRefreshResult(
            blocked=False,
            refreshed=False,
            message_text=str(data.get("message_text") or ""),
            payload=data,
        )

    prose = str(metadata.get("prose_text") or "").strip()
    if not prose:
        return _blocked(data, PRODUCT_REFRESH_FAILED, refreshed_at=None, products=[])

    try:
        rows, refreshed_at = _load_rows(provider, cycle=cycle, clock=clock)
    except ProductFeedError as exc:
        return _blocked(data, PRODUCT_REFRESH_FAILED, refreshed_at=None, products=[])

    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in _row_ids(row):
            by_id.setdefault(key, row)

    heading = str(metadata.get("product_heading") or "محصولات ویژه امروز:")
    decisions = []
    audit_products: list[dict[str, Any]] = []
    blocked_code: str | None = None
    for product_id in selected:
        raw = by_id.get(product_id)
        if raw is None:
            blocked_code = blocked_code or PRODUCT_REFRESH_FAILED
            audit_products.append(
                {
                    "source_product_id": product_id,
                    "sent": False,
                    "reason_code": PRODUCT_REFRESH_FAILED,
                }
            )
            continue
        decision = evaluate_advertising_product(raw, fetched_at=refreshed_at)
        decisions.append(decision)
        if not decision.eligible or decision.product is None:
            reason = _send_reason(decision.reason_codes)
            blocked_code = blocked_code or reason
            audit_products.append(
                {
                    "source_product_id": product_id,
                    "product_code": raw.get("sku") or raw.get("product_code"),
                    "product_name": decision.product.name if decision.product else raw.get("name"),
                    "cash_prepayment_price": (
                        format(decision.cash_prepayment_price, "f")
                        if decision.cash_prepayment_price is not None
                        else None
                    ),
                    "availability": decision.availability,
                    "advertising_tag_valid": MISSING_ADVERTISING_TAG not in decision.reason_codes,
                    "sent": False,
                    "reason_code": reason,
                }
            )
            continue
        audit_products.append(
            {
                "source_product_id": product_id,
                "product_code": decision.product.product_code,
                "product_name": decision.product.name,
                "cash_prepayment_price": format(decision.product.price, "f"),
                "availability": decision.availability,
                "advertising_tag_valid": True,
                "sent": blocked_code is None,
                "reason_code": None,
            }
        )

    rendered_at = _now(clock)
    if blocked_code is not None:
        for item in audit_products:
            item["sent"] = False
        return _blocked(
            data,
            blocked_code,
            refreshed_at=refreshed_at,
            products=audit_products,
            rendered_at=rendered_at,
        )

    products = [item.product for item in decisions if item.product is not None]
    if len(products) != len(selected):
        return _blocked(
            data,
            PRODUCT_REFRESH_FAILED,
            refreshed_at=refreshed_at,
            products=audit_products,
            rendered_at=rendered_at,
        )

    composed = compose_campaign_message(
        prose,
        products,
        heading=heading,
        fetched_at=refreshed_at,
        provider=getattr(provider, "name", "afrakala"),
        unit_label=_unit_label(),
    )
    final_text = append_campaign_footer(composed.final_text)
    audit = {
        "products": audit_products,
        "afrakala_refreshed_at": refreshed_at.isoformat(),
        "rendered_at": rendered_at.isoformat(),
        "final_text": final_text,
        "availability_checked": True,
        "advertising_tag_checked": True,
        "price_authoritative_for_next_send": False,
        "purpose": "send_time_audit",
    }
    metadata["send_time_product_audit"] = audit
    metadata["price_authoritative"] = False
    data["metadata"] = metadata
    data["message_text"] = final_text
    return SendTimeRefreshResult(
        blocked=False,
        refreshed=True,
        message_text=final_text,
        audit=audit,
        payload=data,
    )


def apply_send_time_product_refresh(
    payload: dict[str, Any] | Any,
    *,
    provider: Any | None = None,
    cycle: SendTimeRefreshCycle | None = None,
    clock: datetime | None = None,
) -> SendTimeRefreshResult:
    data = _as_payload(payload)
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if not metadata.get("include_products") or not _selected_ids(metadata):
        return SendTimeRefreshResult(
            blocked=False,
            refreshed=False,
            message_text=str(data.get("message_text") or ""),
            payload=data,
        )
    active = provider
    if active is None:
        from core_engine.services.product_feed.service import get_product_feed_provider

        active = get_product_feed_provider()
    return render_outbound_from_selection(
        data,
        provider=active,
        cycle=cycle,
        clock=clock,
    )


def _blocked(
    data: dict[str, Any],
    code: str,
    *,
    refreshed_at: datetime | None,
    products: list[dict[str, Any]],
    rendered_at: datetime | None = None,
) -> SendTimeRefreshResult:
    rendered = rendered_at or datetime.now(timezone.utc)
    audit = {
        "products": products,
        "afrakala_refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
        "rendered_at": rendered.isoformat(),
        "final_text": None,
        "reason_code": code,
        "price_authoritative_for_next_send": False,
        "purpose": "send_time_block",
    }
    metadata = dict(data.get("metadata") or {})
    metadata["send_time_product_audit"] = audit
    metadata["send_time_block_reason"] = code
    data["metadata"] = metadata
    return SendTimeRefreshResult(
        blocked=True,
        refreshed=True,
        message_text=None,
        reason_code=code,
        reason_message=_reason_message(code),
        audit=audit,
        payload=data,
        retryable=code == PRODUCT_REFRESH_FAILED,
    )
