"""AfraKala Public Bot API freshness semantics (Phase 12)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from core_engine.services.product_feed.afrakala_adapter import (
    SOURCE_PUBLIC_BOT_API,
    normalize_public_bot_product,
)
from core_engine.services.product_feed.dto import AdvertisingProduct, ProductFeedResult
from core_engine.services.product_feed.errors import (
    INSUFFICIENT_ADVERTISING_PRODUCTS,
    PRODUCT_FEED_STALE,
    ProductFeedError,
)
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.service import (
    fetch_current_advertising_products,
    filter_fresh_products,
    product_feed_status,
    select_and_compose,
    set_product_feed_provider,
)

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)
FETCHED_AT = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)
OLD_COMPUTED_AT = "2026-08-16T07:56:34.094+00:00"
OLD_PRODUCT_UPDATED_AT = "2026-08-22T08:29:37.198228+00:00"
LIMIT_SECONDS = 300


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _live_product(
    product_id: str,
    *,
    source_updated_at: datetime | str,
    name: str | None = None,
    price: int = 76_548_750,
) -> AdvertisingProduct:
    stamp = _ts(source_updated_at) if isinstance(source_updated_at, str) else source_updated_at
    return AdvertisingProduct(
        external_id=product_id,
        name=name or f"Product {product_id}",
        price=Decimal(price),
        currency="IRR",
        advertising=True,
        product_code=f"SKU-{product_id}",
        source_updated_at=stamp,
        source=SOURCE_PUBLIC_BOT_API,
        fetched_at=FETCHED_AT,
    )


def _feed(*products: AdvertisingProduct, discarded_invalid: int = 0) -> ProductFeedResult:
    stamps = [p.source_updated_at for p in products if p.source_updated_at]
    return ProductFeedResult(
        products=tuple(products),
        fetched_at=FETCHED_AT,
        provider=SOURCE_PUBLIC_BOT_API,
        discarded_invalid=discarded_invalid,
        source_updated_at=max(stamps) if stamps else None,
    )


@pytest.fixture(autouse=True)
def _reset_provider():
    set_product_feed_provider(None)
    yield
    set_product_feed_provider(None)


@pytest.fixture
def fixed_limit(monkeypatch):
    monkeypatch.setattr(
        "core_engine.services.product_feed.service._max_staleness_seconds",
        lambda: LIMIT_SECONDS,
    )


def test_old_computed_at_product_remains_eligible(fixed_limit):
    """Live evidence: 6-day-old computed_at is still authoritative at fetch time."""
    products = tuple(
        _live_product(
            str(i),
            source_updated_at=OLD_PRODUCT_UPDATED_AT if i == 1 else OLD_COMPUTED_AT,
            name="جارو رباتیک شیاومی X20 PLUS رنگ سفید" if i == 1 else f"P{i}",
        )
        for i in range(1, 6)
    )

    class _Provider:
        name = SOURCE_PUBLIC_BOT_API

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products)

    result = fetch_current_advertising_products(provider=_Provider(), clock=NOW)
    assert len(result.products) == 5
    oldest = next(p for p in result.products if p.external_id == "1")
    assert oldest.source_updated_at == _ts(OLD_PRODUCT_UPDATED_AT)
    assert oldest.price == Decimal("76548750")


def test_old_computed_at_preserved_in_source_updated_at_metadata():
    raw = {
        "id": 285,
        "sku": "AFK-2026-00285",
        "name": "جارو رباتیک شیاومی X20 PLUS رنگ سفید",
        "stock_status": "available",
        "labels": [{"title": "تبلیغات"}],
        "updated_at": OLD_PRODUCT_UPDATED_AT,
        "prices": [
            {
                "sale_price_type_title": "نقدی",
                "final_sale_price": 76_548_750,
                "rounded_sale_price": 76_500_000,
                "computed_at": OLD_COMPUTED_AT,
            }
        ],
    }
    flat, reason = normalize_public_bot_product(raw, default_currency="IRR")
    assert reason is None
    assert flat is not None
    assert flat["source_updated_at"] == OLD_PRODUCT_UPDATED_AT


def test_status_exposes_price_age_diagnostics_without_stale_rejection(fixed_limit):
    products = [
        _live_product("1", source_updated_at=OLD_PRODUCT_UPDATED_AT),
        _live_product("2", source_updated_at="2026-08-22T11:00:12+00:00"),
        _live_product("3", source_updated_at="2026-08-22T09:26:46+00:00"),
    ]

    class _Provider:
        name = SOURCE_PUBLIC_BOT_API

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products, discarded_invalid=6)

    status = product_feed_status(provider=_Provider(), clock=NOW)
    assert status["ok"] is True
    assert status["code"] == "OK"
    assert status["advertising_eligible_count"] == 3
    assert status["fresh_eligible_count"] == 3
    assert status["stale_discarded"] == 0
    assert status["invalid_discarded"] == 6
    assert status["freshness_basis"] == "api_fetch_fetched_at"
    assert status["oldest_price_age_seconds"] is not None
    assert status["oldest_price_age_seconds"] > LIMIT_SECONDS


def test_old_price_age_does_not_raise_product_feed_stale(fixed_limit):
    products = tuple(
        _live_product(str(i), source_updated_at=OLD_COMPUTED_AT) for i in range(5)
    )

    class _Provider:
        name = SOURCE_PUBLIC_BOT_API

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products)

    result = fetch_current_advertising_products(provider=_Provider(), clock=NOW)
    assert len(result.products) == 5


def test_stale_cached_fetch_raises_product_feed_stale(fixed_limit):
    products = tuple(_live_product(str(i), source_updated_at=OLD_COMPUTED_AT) for i in range(3))
    stale_fetch = FETCHED_AT - timedelta(hours=2)

    class _Provider:
        name = SOURCE_PUBLIC_BOT_API

        def fetch_advertising_products(self, *, clock=None):
            return ProductFeedResult(
                products=products,
                fetched_at=stale_fetch,
                provider=SOURCE_PUBLIC_BOT_API,
            )

    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products(provider=_Provider(), clock=NOW)
    assert exc.value.code == PRODUCT_FEED_STALE
    assert exc.value.details["fetch_age_seconds"] > LIMIT_SECONDS


def test_insufficient_eligible_when_fewer_than_minimum(fixed_limit):
    products = (
        _live_product("1", source_updated_at=OLD_COMPUTED_AT),
        _live_product("2", source_updated_at=OLD_COMPUTED_AT),
    )

    class _Provider:
        name = SOURCE_PUBLIC_BOT_API

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products)

    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products(provider=_Provider(), clock=NOW)
    assert exc.value.code == INSUFFICIENT_ADVERTISING_PRODUCTS


def test_selection_receives_all_eligible_products(fixed_limit):
    products = tuple(
        _live_product(str(i), source_updated_at=OLD_COMPUTED_AT) for i in range(5)
    )
    filtered, stats = filter_fresh_products(_feed(*products), clock=NOW, limit_seconds=LIMIT_SECONDS)
    assert stats.fresh_eligible == 5
    composition = select_and_compose("سلام", filtered)
    assert composition.snapshot is not None
    assert len(composition.snapshot.products) >= 3


def test_legacy_fake_provider_still_rejects_old_price_age(fixed_limit):
    old = (NOW - timedelta(hours=2)).isoformat()
    rows = [
        {
            "sku": f"S{i}",
            "title": f"P{i}",
            "cash_price": 1000 + i,
            "currency": "IRR",
            "advertising": True,
            "updated_at": old,
        }
        for i in range(3)
    ]
    provider = FakeProductFeedProvider(rows)
    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products(provider=provider, clock=NOW)
    assert exc.value.code == PRODUCT_FEED_STALE


def test_legacy_fake_provider_without_timestamps_still_compatible(fixed_limit):
    rows = [
        {
            "sku": f"S{i}",
            "title": f"P{i}",
            "cash_price": 1000 + i,
            "currency": "IRR",
            "advertising": True,
        }
        for i in range(5)
    ]
    provider = FakeProductFeedProvider(rows)
    result = fetch_current_advertising_products(provider=provider, clock=NOW)
    assert len(result.products) == 5
