"""HTTP JSON product-feed adapter.

Uses the fully configured URL from settings. Does not invent REST paths.
LIVE binding is CONFIG_PENDING when AFRAKALA_PRODUCT_API_BASE_URL is empty.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from core_engine.config import get_settings
from core_engine.services.product_feed.canonical import canonicalize_product_row
from core_engine.services.product_feed.dto import AdvertisingProduct, ProductFeedResult
from core_engine.services.product_feed.errors import (
    CONFIG_PENDING,
    PRODUCT_FEED_INVALID_RESPONSE,
    PRODUCT_FEED_TIMEOUT,
    PRODUCT_FEED_UNAVAILABLE,
    ProductFeedError,
)

logger = logging.getLogger("core_engine.services.product_feed.http")

LIST_KEYS = ("data", "items", "products", "results")
MAX_BODY_BYTES = 2_000_000


class HttpJsonProductFeedProvider:
    name = "http_json"

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        timeout_seconds: float = 8,
        default_currency: str = "IRR",
    ) -> None:
        self.base_url = (base_url or "").strip()
        self._token = token or ""
        self.timeout_seconds = timeout_seconds
        self.default_currency = default_currency

    @classmethod
    def from_settings(cls) -> "HttpJsonProductFeedProvider":
        settings = get_settings()
        return cls(
            base_url=getattr(settings, "AFRAKALA_PRODUCT_API_BASE_URL", "") or "",
            token=getattr(settings, "AFRAKALA_PRODUCT_API_TOKEN", "") or "",
            timeout_seconds=float(
                getattr(settings, "AFRAKALA_PRODUCT_API_TIMEOUT_SECONDS", 8) or 8
            ),
            default_currency=str(
                getattr(settings, "AFRAKALA_PRODUCT_PRICE_CURRENCY", "IRR") or "IRR"
            ),
        )

    def fetch_advertising_products(
        self,
        *,
        clock: datetime | None = None,
    ) -> ProductFeedResult:
        if not self.base_url:
            raise ProductFeedError(
                CONFIG_PENDING,
                "قرارداد زنده API افراکالا پیکربندی نشده است.",
                details={"live_binding": CONFIG_PENDING},
            )
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProductFeedError(
                PRODUCT_FEED_UNAVAILABLE,
                "آدرس API محصولات نامعتبر است.",
            )

        now = clock or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer [REDACTED]"
        safe_headers = dict(headers)
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        timeout = httpx.Timeout(
            connect=min(3.0, self.timeout_seconds),
            read=self.timeout_seconds,
            write=3.0,
            pool=3.0,
        )
        logger.info(
            "event=product_feed_fetch_started provider=%s url_host=%s",
            self.name,
            parsed.netloc,
        )
        raw_payload = self._get_json(headers, timeout, safe_headers, parsed.netloc)
        items = _extract_items(raw_payload)
        products: list[AdvertisingProduct] = []
        discarded = 0
        diagnostics: list[str] = []
        for row in items:
            product, reason = canonicalize_product_row(
                row,
                default_currency=self.default_currency,
                default_source="afrakala",
                fetched_at=now,
            )
            if product is None:
                discarded += 1
                if reason:
                    diagnostics.append(reason)
                continue
            products.append(product)
        source_updated = None
        stamps = [p.source_updated_at for p in products if p.source_updated_at]
        if stamps:
            source_updated = max(stamps)
        logger.info(
            "event=product_feed_fetch_succeeded provider=%s product_count=%s discarded=%s",
            self.name,
            len(products),
            discarded,
        )
        return ProductFeedResult(
            products=tuple(products),
            fetched_at=now,
            provider=self.name,
            discarded_invalid=discarded,
            source_updated_at=source_updated,
            diagnostics=tuple(diagnostics[:20]),
        )

    def _get_json(
        self,
        headers: dict[str, str],
        timeout: httpx.Timeout,
        safe_headers: dict[str, str],
        host: str,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                    response = client.get(self.base_url, headers=headers)
                break
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt == 0:
                    continue
                logger.warning(
                    "event=product_feed_fetch_failed provider=%s code=%s host=%s",
                    self.name,
                    PRODUCT_FEED_TIMEOUT,
                    host,
                )
                raise ProductFeedError(PRODUCT_FEED_TIMEOUT) from exc
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == 0:
                    continue
                logger.warning(
                    "event=product_feed_fetch_failed provider=%s code=%s host=%s",
                    self.name,
                    PRODUCT_FEED_UNAVAILABLE,
                    host,
                )
                raise ProductFeedError(PRODUCT_FEED_UNAVAILABLE) from exc
        else:
            raise ProductFeedError(PRODUCT_FEED_UNAVAILABLE) from last_error

        if response.status_code != 200:
            logger.warning(
                "event=product_feed_fetch_failed provider=%s status=%s host=%s headers=%s",
                self.name,
                response.status_code,
                host,
                {k: v for k, v in safe_headers.items() if k.lower() != "authorization"},
            )
            raise ProductFeedError(PRODUCT_FEED_UNAVAILABLE)

        if len(response.content) > MAX_BODY_BYTES:
            raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE)

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE) from exc


def _extract_items(raw_payload: Any) -> list[Any]:
    if isinstance(raw_payload, list):
        return raw_payload
    if isinstance(raw_payload, dict):
        for key in LIST_KEYS:
            value = raw_payload.get(key)
            if isinstance(value, list):
                return value
        raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE)
    raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE)
