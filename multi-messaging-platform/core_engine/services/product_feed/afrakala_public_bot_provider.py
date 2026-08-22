"""AfraKala public bot products API provider (paginated, bearer auth)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from core_engine.config import get_settings
from core_engine.services.product_feed.afrakala_adapter import (
    SOURCE_PUBLIC_BOT_API,
    normalize_public_bot_product,
)
from core_engine.services.product_feed.canonical import canonicalize_product_row
from core_engine.services.product_feed.dto import AdvertisingProduct, ProductFeedResult
from core_engine.services.product_feed.errors import (
    CONFIG_PENDING,
    PRODUCT_FEED_INVALID_RESPONSE,
    PRODUCT_FEED_TIMEOUT,
    PRODUCT_FEED_UNAVAILABLE,
    ProductFeedError,
)

logger = logging.getLogger("core_engine.services.product_feed.afrakala_public_bot")

MAX_BODY_BYTES = 2_000_000
DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 200
PRODUCTS_PATH = "/api/public/bot/products"


class AfraKalaPublicBotProductFeedProvider:
    name = "afrakala_public_bot_api"

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        timeout_seconds: float = 8,
        default_currency: str = "IRR",
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self._token = token or ""
        self.timeout_seconds = timeout_seconds
        self.default_currency = default_currency
        self.page_size = max(1, min(100, int(page_size)))

    @classmethod
    def from_settings(cls) -> "AfraKalaPublicBotProductFeedProvider":
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
            page_size=int(
                getattr(settings, "AFRAKALA_PRODUCT_API_PAGE_SIZE", DEFAULT_PAGE_SIZE)
                or DEFAULT_PAGE_SIZE
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

        raw_products = self._fetch_all_pages(headers, timeout, parsed.netloc)
        products: list[AdvertisingProduct] = []
        discarded = 0
        diagnostics: list[str] = []
        for raw in raw_products:
            flat, reason = normalize_public_bot_product(
                raw,
                default_currency=self.default_currency,
            )
            if flat is None:
                discarded += 1
                if reason:
                    diagnostics.append(reason)
                continue
            product, canon_reason = canonicalize_product_row(
                flat,
                default_currency=self.default_currency,
                default_source=SOURCE_PUBLIC_BOT_API,
                fetched_at=now,
            )
            if product is None:
                discarded += 1
                if canon_reason:
                    diagnostics.append(canon_reason)
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
            diagnostics=tuple(diagnostics[:50]),
        )

    def _fetch_all_pages(
        self,
        headers: dict[str, str],
        timeout: httpx.Timeout,
        host: str,
    ) -> list[dict[str, Any]]:
        seen_ids: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            url = (
                f"{self.base_url}{PRODUCTS_PATH}"
                f"?page={page}&page_size={self.page_size}"
            )
            payload = self._get_json(url, headers, timeout, host)
            if not isinstance(payload, dict):
                raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE)
            items = payload.get("products")
            pagination = payload.get("pagination")
            if not isinstance(items, list):
                raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE)
            if not isinstance(pagination, dict):
                raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE)
            has_more = pagination.get("has_more")
            if not isinstance(has_more, bool):
                raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE)

            for item in items:
                if not isinstance(item, dict):
                    continue
                product_id = str(item.get("id", "")).strip()
                if not product_id:
                    continue
                seen_ids.setdefault(product_id, item)

            if not has_more:
                break
            page += 1
            if page > MAX_PAGES:
                raise ProductFeedError(
                    PRODUCT_FEED_INVALID_RESPONSE,
                    details={"reason": "pagination_limit_exceeded"},
                )
        return list(seen_ids.values())

    def _get_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout: httpx.Timeout,
        host: str,
    ) -> Any:
        last_error: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                    response = client.get(url, headers=headers)
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

        assert response is not None
        if response.status_code in {401, 403}:
            logger.warning(
                "event=product_feed_fetch_failed provider=%s status=%s host=%s auth=denied",
                self.name,
                response.status_code,
                host,
            )
            raise ProductFeedError(PRODUCT_FEED_UNAVAILABLE)
        if response.status_code == 429 or response.status_code >= 500:
            logger.warning(
                "event=product_feed_fetch_failed provider=%s status=%s host=%s",
                self.name,
                response.status_code,
                host,
            )
            raise ProductFeedError(PRODUCT_FEED_UNAVAILABLE)
        if response.status_code != 200:
            logger.warning(
                "event=product_feed_fetch_failed provider=%s status=%s host=%s",
                self.name,
                response.status_code,
                host,
            )
            raise ProductFeedError(PRODUCT_FEED_UNAVAILABLE)

        if len(response.content) > MAX_BODY_BYTES:
            raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE)

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProductFeedError(PRODUCT_FEED_INVALID_RESPONSE) from exc
