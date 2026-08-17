"""ProductFeedProvider protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core_engine.services.product_feed.dto import ProductFeedResult


class ProductFeedProvider(Protocol):
    name: str

    def fetch_advertising_products(
        self,
        *,
        clock: datetime | None = None,
    ) -> ProductFeedResult: ...
