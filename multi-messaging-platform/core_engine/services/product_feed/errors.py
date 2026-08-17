"""Structured product-feed failures. Fail closed for include_products campaigns."""

from __future__ import annotations

PERSIAN_FEED_UNAVAILABLE = (
    "اطلاعات محصولات و قیمت‌های لحظه‌ای در دسترس نیست؛ "
    "آماده‌سازی کمپین شامل محصولات متوقف شد."
)

PRODUCT_FEED_UNAVAILABLE = "PRODUCT_FEED_UNAVAILABLE"
PRODUCT_FEED_TIMEOUT = "PRODUCT_FEED_TIMEOUT"
PRODUCT_FEED_INVALID_RESPONSE = "PRODUCT_FEED_INVALID_RESPONSE"
PRODUCT_FEED_STALE = "PRODUCT_FEED_STALE"
PRODUCT_FEED_EMPTY = "PRODUCT_FEED_EMPTY"
PRODUCT_PRICE_INVALID = "PRODUCT_PRICE_INVALID"
INSUFFICIENT_ADVERTISING_PRODUCTS = "INSUFFICIENT_ADVERTISING_PRODUCTS"
CONFIG_PENDING = "CONFIG_PENDING"

PRODUCT_FEED_ERROR_CODES = frozenset(
    {
        PRODUCT_FEED_UNAVAILABLE,
        PRODUCT_FEED_TIMEOUT,
        PRODUCT_FEED_INVALID_RESPONSE,
        PRODUCT_FEED_STALE,
        PRODUCT_FEED_EMPTY,
        PRODUCT_PRICE_INVALID,
        INSUFFICIENT_ADVERTISING_PRODUCTS,
        CONFIG_PENDING,
    }
)


class ProductFeedError(Exception):
    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        details: dict | None = None,
    ) -> None:
        self.code = code
        self.message = message or PERSIAN_FEED_UNAVAILABLE
        self.details = details or {}
        super().__init__(self.message)

    def http_detail(self) -> dict:
        payload = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload
