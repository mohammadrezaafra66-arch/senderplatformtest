"""Canonical advertising eligibility. One authority for tag, stock, and cash price."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core_engine.services.product_feed.afrakala_adapter import (
    final_sale_price_issue,
    select_cash_price,
)
from core_engine.services.product_feed.canonical import parse_price
from core_engine.services.product_feed.dto import AdvertisingProduct

ADVERTISING_TAG = "تبلیغات"
CASH_PRICE_LABEL = "قیمت نقدی (پیش واریز)"

MISSING_ADVERTISING_TAG = "MISSING_ADVERTISING_TAG"
PRODUCT_UNAVAILABLE = "PRODUCT_UNAVAILABLE"
CASH_PREPAYMENT_PRICE_MISSING = "CASH_PREPAYMENT_PRICE_MISSING"
INVALID_PRICE = "INVALID_PRICE"
INVALID_PRODUCT_DATA = "INVALID_PRODUCT_DATA"

_AVAILABLE_STOCK = frozenset({"available"})
_UNAVAILABLE_STOCK = frozenset(
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


def normalize_product_tag(value: Any) -> str:
    """Trim edges and collapse internal whitespace. Do not case-fold."""
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def exact_advertising_tag(value: Any) -> bool:
    return normalize_product_tag(value) == ADVERTISING_TAG


def product_tags(raw: dict[str, Any]) -> list[str]:
    labels = raw.get("labels")
    if labels is None:
        labels = raw.get("tags") if raw.get("tags") is not None else raw.get("tag")
    if labels is None:
        return []
    if isinstance(labels, dict):
        labels = [labels]
    if isinstance(labels, str):
        labels = [labels]
    if not isinstance(labels, list):
        return []
    tags: list[str] = []
    for item in labels:
        if isinstance(item, dict):
            title = normalize_product_tag(item.get("title") or item.get("name"))
        else:
            title = normalize_product_tag(item)
        if title:
            tags.append(title)
    return tags


def has_exact_advertising_tag(raw: dict[str, Any]) -> bool:
    return any(exact_advertising_tag(tag) for tag in product_tags(raw))


def _entity_name(value: Any) -> str:
    """Brand and category arrive as strings or as {name: ...} objects."""
    if isinstance(value, dict):
        return normalize_product_tag(value.get("name") or value.get("title"))
    return normalize_product_tag(value)


def availability_state(raw: dict[str, Any]) -> str:
    """available, unavailable, or unknown. Unknown fails closed.

    Live list rows are eligible only for status=active and stock_status=available.
    """
    status = normalize_product_tag(raw.get("status")).lower()
    stock = normalize_product_tag(raw.get("stock_status"))
    stock_key = stock.lower()
    if status != "active":
        return "unavailable"
    if not stock:
        return "unknown"
    if stock_key in _UNAVAILABLE_STOCK or stock in _UNAVAILABLE_STOCK:
        return "unavailable"
    if stock_key in _AVAILABLE_STOCK:
        return "available"
    return "unknown"


@dataclass(frozen=True, slots=True)
class AdvertisingEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]
    product: AdvertisingProduct | None
    tags: tuple[str, ...]
    availability: str
    cash_prepayment_price: Decimal | None
    brand: str | None
    category: str | None


def evaluate_advertising_product(
    raw: Any,
    *,
    default_currency: str = "IRR",
    fetched_at: datetime | None = None,
    source: str = "afrakala_public_bot_api",
) -> AdvertisingEligibility:
    if not isinstance(raw, dict):
        return AdvertisingEligibility(
            eligible=False,
            reason_codes=(INVALID_PRODUCT_DATA,),
            product=None,
            tags=(),
            availability="unknown",
            cash_prepayment_price=None,
            brand=None,
            category=None,
        )

    tags = tuple(product_tags(raw))
    reasons: list[str] = []
    if not has_exact_advertising_tag(raw):
        reasons.append(MISSING_ADVERTISING_TAG)

    availability = availability_state(raw)
    if availability != "available":
        reasons.append(PRODUCT_UNAVAILABLE)

    cash = select_cash_price(raw.get("prices"))
    price = cash[0] if cash is not None else None
    if price is None and raw.get("cash_price") is not None and not raw.get("prices"):
        # Already-flattened rows may carry the selected cash price only.
        price = parse_price(raw.get("cash_price"))
    if price is None:
        if raw.get("prices") and final_sale_price_issue(raw.get("prices")) == "invalid_final_sale_price":
            reasons.append(INVALID_PRICE)
        else:
            reasons.append(CASH_PREPAYMENT_PRICE_MISSING)
    elif price <= 0:
        reasons.append(INVALID_PRICE)
        price = None

    external_id = normalize_product_tag(raw.get("id") or raw.get("external_id") or raw.get("sku"))
    name = normalize_product_tag(raw.get("name") or raw.get("title"))
    if not external_id or not name:
        reasons.append(INVALID_PRODUCT_DATA)

    brand = _entity_name(raw.get("brand")) or None
    category = _entity_name(raw.get("category")) or None
    product = None
    eligible = not reasons
    if eligible and price is not None:
        now = fetched_at or datetime.now(timezone.utc)
        product = AdvertisingProduct(
            external_id=external_id,
            name=name,
            price=price,
            currency=normalize_product_tag(raw.get("currency")) or default_currency,
            advertising=True,
            product_code=normalize_product_tag(raw.get("sku") or raw.get("product_code")) or None,
            source=source,
            fetched_at=now,
        )
    return AdvertisingEligibility(
        eligible=eligible,
        reason_codes=tuple(reasons),
        product=product,
        tags=tags,
        availability=availability,
        cash_prepayment_price=price,
        brand=brand,
        category=category,
    )


def filter_eligible_products(
    decisions: list[AdvertisingEligibility],
    *,
    brand: str | None = None,
    category: str | None = None,
) -> list[AdvertisingEligibility]:
    brand_needle = normalize_product_tag(brand)
    category_needle = normalize_product_tag(category)
    out: list[AdvertisingEligibility] = []
    for item in decisions:
        if not item.eligible:
            continue
        if brand_needle and normalize_product_tag(item.brand) != brand_needle:
            continue
        if category_needle and normalize_product_tag(item.category) != category_needle:
            continue
        out.append(item)
    return out
