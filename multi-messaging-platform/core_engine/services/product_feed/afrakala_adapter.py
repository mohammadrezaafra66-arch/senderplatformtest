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


def _price_from_cash_record(record: dict[str, Any]) -> Decimal | None:
    final_price = parse_price(record.get("final_sale_price"))
    if final_price is not None:
        return final_price
    return parse_price(record.get("rounded_sale_price"))


def select_cash_price(
    prices: Any,
) -> tuple[Decimal, datetime | None] | None:
    """Pick newest نقدی price by computed_at; never use unrelated price types."""
    if not isinstance(prices, list):
        return None

    candidates: list[tuple[datetime, Decimal, datetime | None]] = []
    for record in prices:
        if not isinstance(record, dict):
            continue
        if normalize_whitespace(record.get("sale_price_type_title")) != CASH_PRICE_TYPE_TITLE:
            continue
        price = _price_from_cash_record(record)
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

    stock_status = raw.get("stock_status")
    if is_unavailable_stock(stock_status):
        return None, "unavailable_stock"

    cash = select_cash_price(raw.get("prices"))
    if cash is None:
        return None, "missing_cash_price"
    price, price_computed_at = cash

    external_id = normalize_whitespace(raw.get("id"))
    if not external_id:
        return None, "missing_external_id"

    name = normalize_whitespace(raw.get("name"))
    if not name:
        return None, "missing_name"

    product_code = normalize_whitespace(raw.get("sku")) or None

    source_updated_at = _parse_dt(raw.get("updated_at"))
    if price_computed_at is not None:
        if source_updated_at is None or price_computed_at > source_updated_at:
            source_updated_at = price_computed_at

    flat: dict[str, Any] = {
        "id": external_id,
        "sku": product_code,
        "name": name,
        "cash_price": price,
        "currency": default_currency,
        "advertising": True,
        "source_updated_at": source_updated_at.isoformat() if source_updated_at else None,
        "_stock_status": stock_status,
    }
    # Preserve nested-label path for canonical safety checks when bool is absent.
    if has_explicit_advertising_label(raw.get("labels")):
        flat["labels"] = raw.get("labels")

    # Sanity: flat row must still pass explicit advertising checks.
    if not is_explicitly_advertising(flat):
        return None, "not_advertising"

    return flat, None
