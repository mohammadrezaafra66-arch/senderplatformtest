"""Normalize AfraKala public bot API products for the canonical feed pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core_engine.services.product_feed.canonical import (
    _parse_dt,
    is_explicitly_advertising,
    parse_price,
)

ADVERTISING_LABEL_TITLE = "تبلیغات"
CASH_PRICE_TYPE_TITLE = "نقدی"
CASH_PRICE_TYPE_CODE = "cash_price"
PREPAYMENT_SETTLEMENT_TYPE_CODE = "cash"
SOURCE_PUBLIC_BOT_API = "afrakala_public_bot_api"

_UNAVAILABLE_STOCK_STATUSES = frozenset(
    {
        "out_of_stock",
        "outofstock",
        "unavailable",
        "not_available",
        "not available",
        "ناموجود",
        "عدم موجودی",
    }
)


def normalize_whitespace(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def label_title_matches_advertising(title: Any) -> bool:
    return normalize_whitespace(title) == ADVERTISING_LABEL_TITLE


def has_explicit_advertising_label(labels: Any) -> bool:
    """True only when labels explicitly include title == تبلیغات."""
    if labels is None:
        return False
    if isinstance(labels, dict):
        labels = [labels]
    if isinstance(labels, str):
        return label_title_matches_advertising(labels)
    if not isinstance(labels, list):
        return False
    for item in labels:
        if isinstance(item, dict):
            if label_title_matches_advertising(item.get("title")):
                return True
        elif label_title_matches_advertising(item):
            return True
    return False


def is_unavailable_stock(stock_status: Any) -> bool:
    normalized = normalize_whitespace(stock_status).lower()
    if not normalized:
        return False
    return normalized in _UNAVAILABLE_STOCK_STATUSES


def _price_from_prepayment_record(
    record: dict[str, Any],
) -> Decimal | None:
    """Use only AfraKala's rounded/display price for the exact prepayment row."""
    return parse_price(record.get("rounded_sale_price"))


def select_cash_price(
    prices: Any,
) -> tuple[Decimal, datetime | None] | None:
    """Select only cash_price + cash (prepayment), using rounded_sale_price.

    Do not fall back to another settlement type or final_sale_price.
    """
    if not isinstance(prices, list):
        return None

    candidates: list[tuple[datetime, Decimal, datetime | None]] = []
    for record in prices:
        if not isinstance(record, dict):
            continue

        price_type_code = normalize_whitespace(
            record.get("sale_price_type_code")
        ).lower()
        settlement_type_code = normalize_whitespace(
            record.get("settlement_type_code")
        ).lower()

        if price_type_code != CASH_PRICE_TYPE_CODE:
            continue
        if settlement_type_code != PREPAYMENT_SETTLEMENT_TYPE_CODE:
            continue

        price = _price_from_prepayment_record(record)
        if price is None:
            continue

        computed_at = _parse_dt(record.get("computed_at"))
        sort_key = computed_at or datetime.min.replace(tzinfo=timezone.utc)
        candidates.append((sort_key, price, computed_at))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, price, computed_at = candidates[0]
    return price, computed_at


def normalize_public_bot_product(
    raw: Any,
    *,
    default_currency: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Map one AfraKala public-bot product into a flat row for canonicalize_product_row."""
    if not isinstance(raw, dict):
        return None, "row_not_object"

    if not has_explicit_advertising_label(raw.get("labels")):
        return None, "not_advertising"

    from core_engine.services.product_feed.advertising_eligibility import (
        CASH_PREPAYMENT_PRICE_MISSING,
        INVALID_PRICE,
        INVALID_PRODUCT_DATA,
        MISSING_ADVERTISING_TAG,
        PRODUCT_UNAVAILABLE,
        evaluate_advertising_product,
    )

    decision = evaluate_advertising_product(
        raw,
        default_currency=default_currency,
        source=SOURCE_PUBLIC_BOT_API,
    )
    if not decision.eligible or decision.product is None:
        codes = set(decision.reason_codes)
        status = normalize_whitespace(raw.get("status")).lower()
        if status and status != "active":
            return None, "inactive_product"
        if PRODUCT_UNAVAILABLE in codes:
            stock = normalize_whitespace(raw.get("stock_status"))
            if not stock:
                return None, "unknown_availability"
            return None, "unavailable_stock"
        if CASH_PREPAYMENT_PRICE_MISSING in codes or INVALID_PRICE in codes:
            return None, "missing_cash_price"
        if MISSING_ADVERTISING_TAG in codes:
            return None, "not_advertising"
        if INVALID_PRODUCT_DATA in codes:
            if not normalize_whitespace(raw.get("id")):
                return None, "missing_external_id"
            return None, "missing_name"
        return None, "not_advertising"

    price = decision.cash_prepayment_price
    if price is None:
        return None, "missing_cash_price"

    source_updated_at = _parse_dt(raw.get("updated_at"))
    cash = select_cash_price(raw.get("prices"))
    price_computed_at = cash[1] if cash else None
    if price_computed_at is not None:
        if source_updated_at is None or price_computed_at > source_updated_at:
            source_updated_at = price_computed_at

    flat: dict[str, Any] = {
        "id": decision.product.external_id,
        "sku": decision.product.product_code,
        "name": decision.product.name,
        "cash_price": price,
        "currency": default_currency,
        "advertising": True,
        "source_updated_at": source_updated_at.isoformat() if source_updated_at else None,
        "_stock_status": raw.get("stock_status"),
        "labels": raw.get("labels"),
        "brand": decision.brand,
        "category": decision.category,
    }
    return flat, None
