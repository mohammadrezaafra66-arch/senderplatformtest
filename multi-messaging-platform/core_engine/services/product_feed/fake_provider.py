"""In-process fake provider for tests. Never hits the network."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core_engine.services.product_feed.canonical import canonicalize_product_row
from core_engine.services.product_feed.dto import AdvertisingProduct, ProductFeedResult
from core_engine.services.product_feed.errors import ProductFeedError


class FakeProductFeedProvider:
    name = "fake"

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        fail_code: str | None = None,
        fail_message: str | None = None,
        default_currency: str = "IRR",
        stale: bool = False,
    ) -> None:
        self.rows = list(rows or [])
        self.fail_code = fail_code
        self.fail_message = fail_message
        self.default_currency = default_currency
        self.stale = stale
        self.call_count = 0

    def fetch_advertising_products(
        self,
        *,
        clock: datetime | None = None,
    ) -> ProductFeedResult:
        self.call_count += 1
        if self.fail_code:
            raise ProductFeedError(self.fail_code, self.fail_message)
        now = clock or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        products: list[AdvertisingProduct] = []
        discarded = 0
        diagnostics: list[str] = []
        for raw in self.rows:
            product, reason = canonicalize_product_row(
                raw,
                default_currency=self.default_currency,
                default_source="fake",
                fetched_at=now,
            )
            if product is None:
                discarded += 1
                if reason:
                    diagnostics.append(reason)
                continue
            products.append(product)
        source_updated = None
        if products:
            stamps = [p.source_updated_at for p in products if p.source_updated_at]
            if stamps:
                source_updated = max(stamps)
        return ProductFeedResult(
            products=tuple(products),
            fetched_at=now,
            provider=self.name,
            discarded_invalid=discarded,
            source_updated_at=source_updated,
            diagnostics=tuple(diagnostics),
        )
