"""Controlled 3–5 product selection. Testable via injected RNG."""

from __future__ import annotations

import random
from collections.abc import Sequence

from core_engine.services.product_feed.dto import AdvertisingProduct
from core_engine.services.product_feed.errors import (
    INSUFFICIENT_ADVERTISING_PRODUCTS,
    PRODUCT_FEED_EMPTY,
    ProductFeedError,
)

MIN_PRODUCTS = 3
MAX_PRODUCTS = 5


def select_advertising_products(
    eligible: Sequence[AdvertisingProduct],
    rng: random.Random | None = None,
    *,
    min_count: int = MIN_PRODUCTS,
    max_count: int = MAX_PRODUCTS,
) -> tuple[AdvertisingProduct, ...]:
    unique: list[AdvertisingProduct] = []
    seen: set[str] = set()
    for product in eligible:
        if not product.advertising:
            continue
        if product.external_id in seen:
            continue
        seen.add(product.external_id)
        unique.append(product)

    if not unique:
        raise ProductFeedError(PRODUCT_FEED_EMPTY)
    if len(unique) < min_count:
        raise ProductFeedError(
            INSUFFICIENT_ADVERTISING_PRODUCTS,
            details={"eligible": len(unique), "required": min_count},
        )

    chooser = rng or random.Random()
    upper = min(max_count, len(unique))
    count = chooser.randint(min_count, upper)
    selected = chooser.sample(unique, count)
    ids = [item.external_id for item in selected]
    if len(ids) != len(set(ids)):
        raise ProductFeedError(INSUFFICIENT_ADVERTISING_PRODUCTS)
    return tuple(selected)


def select_explicit_products(
    eligible: Sequence[AdvertisingProduct],
    product_ids: Sequence[str],
) -> tuple[AdvertisingProduct, ...]:
    """Operator selection. Ineligible or unknown ids cannot be selected."""
    by_id = {product.external_id: product for product in eligible if product.advertising}
    selected: list[AdvertisingProduct] = []
    missing: list[str] = []
    for product_id in product_ids:
        key = str(product_id).strip()
        if not key:
            continue
        product = by_id.get(key)
        if product is None:
            missing.append(key)
            continue
        selected.append(product)
    if missing or not selected:
        raise ProductFeedError(
            INSUFFICIENT_ADVERTISING_PRODUCTS,
            details={"missing_or_ineligible": missing or list(product_ids)},
        )
    return tuple(selected)
