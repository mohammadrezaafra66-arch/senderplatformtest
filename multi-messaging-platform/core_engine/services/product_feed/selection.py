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
