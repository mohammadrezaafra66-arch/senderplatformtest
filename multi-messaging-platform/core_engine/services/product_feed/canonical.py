"""Map provider rows to canonical AdvertisingProduct.

Advertising eligibility is explicit only — never inferred from price, name,
category, stock, or discount.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from core_engine.services.product_feed.dto import AdvertisingProduct

_TRUE_VALUES = {True, 1, "1", "true", "True", "yes", "YES"}
_AD_TAG_TOKENS = {
    "advertising",
    "advertisement",
    "promo",
    "promotion",
    "promotional",
    "تبلیغ",
    "تبلیغاتی",
}
_ID_KEYS = ("external_id", "id", "sku", "product_id", "product_code", "code")
_NAME_KEYS = ("name", "title")
_PRICE_KEYS = ("price", "cash_price", "current_price")
_CODE_KEYS = ("product_code", "sku", "code")
_CURRENCY_KEYS = ("currency", "price_currency", "unit")
_UPDATED_KEYS = ("source_updated_at", "updated_at", "updatedAt")
_AD_BOOL_KEYS = (
    "advertising",
    "is_advertising",
    "is_promo",
    "promotional",
    "advertising_tagged",
)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_explicitly_advertising(raw: dict[str, Any]) -> bool:
    for key in _AD_BOOL_KEYS:
        if key in raw and raw[key] in _TRUE_VALUES:
            return True
    tags = raw.get("tags") or raw.get("labels") or raw.get("tag")
    if isinstance(tags, str):
        tags = [tags]
    if isinstance(tags, list):
        tokens = {str(item).strip().lower() for item in tags}
        if tokens & {t.lower() for t in _AD_TAG_TOKENS}:
            return True
    return False


def parse_price(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        # Reject binary-float guesswork; require exact numeric strings/ints.
        return None
    else:
        text = str(value).strip().replace(",", "").replace("٬", "")
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    if number < 0:
        return None
    return number


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonicalize_product_row(
    raw: Any,
    *,
    default_currency: str,
    default_source: str,
    fetched_at: datetime,
) -> tuple[AdvertisingProduct | None, str | None]:
    if not isinstance(raw, dict):
        return None, "row_not_object"
    if not is_explicitly_advertising(raw):
        return None, "not_advertising"

    external_id = ""
    for key in _ID_KEYS:
        external_id = _as_str(raw.get(key))
        if external_id:
            break
    if not external_id:
        return None, "missing_external_id"

    name = ""
    for key in _NAME_KEYS:
        name = _as_str(raw.get(key))
        if name:
            break
    if not name:
        return None, "missing_name"

    price: Decimal | None = None
    for key in _PRICE_KEYS:
        if key in raw:
            price = parse_price(raw.get(key))
            if price is not None:
                break
    if price is None:
        return None, "invalid_price"

    currency = ""
    for key in _CURRENCY_KEYS:
        currency = _as_str(raw.get(key))
        if currency:
            break
    if not currency:
        currency = default_currency
    if not currency:
        return None, "missing_currency"

    product_code = None
    for key in _CODE_KEYS:
        value = _as_str(raw.get(key))
        if value:
            product_code = value
            break

    source_updated_at = None
    for key in _UPDATED_KEYS:
        source_updated_at = _parse_dt(raw.get(key))
        if source_updated_at is not None:
            break

    return (
        AdvertisingProduct(
            external_id=external_id,
            name=name,
            price=price,
            currency=currency,
            advertising=True,
            product_code=product_code,
            source_updated_at=source_updated_at,
            source=default_source,
            fetched_at=fetched_at,
        ),
        None,
    )
