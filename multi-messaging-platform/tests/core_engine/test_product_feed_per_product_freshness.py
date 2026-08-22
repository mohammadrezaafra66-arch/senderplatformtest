"""Per-product freshness filtering (Phase 12)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

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

NOW = datetime(2026, 8, 22, 11, 3, 0, tzinfo=timezone.utc)
FETCHED_AT = datetime(2026, 8, 22, 11, 11, 11, tzinfo=timezone.utc)
LIMIT_SECONDS = 300


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _live_product(
    product_id: str,
    *,
    source_updated_at: datetime | str | None,
    name: str | None = None,
) -> AdvertisingProduct:
    stamp = None
    if isinstance(source_updated_at, str):
        stamp = _ts(source_updated_at)
    elif isinstance(source_updated_at, datetime):
        stamp = source_updated_at
    return AdvertisingProduct(
        external_id=product_id,
        name=name or f"Product {product_id}",
        price=Decimal("28500000"),
        currency="IRR",
        advertising=True,
        product_code=f"SKU-{product_id}",
        source_updated_at=stamp,
        source="afrakala_public_bot_api",
        fetched_at=FETCHED_AT,
    )


def _feed(*products: AdvertisingProduct, discarded_invalid: int = 0) -> ProductFeedResult:
    stamps = [p.source_updated_at for p in products if p.source_updated_at]
    return ProductFeedResult(
        products=tuple(products),
        fetched_at=FETCHED_AT,
        provider="afrakala_public_bot_api",
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


# A. 5 eligible / all fresh -> success
def test_five_eligible_all_fresh_success(fixed_limit):
    products = tuple(
        _live_product(str(i), source_updated_at=f"2026-08-22T11:0{i}:00+00:00")
        for i in range(5)
    )
    filtered, stats = filter_fresh_products(_feed(*products), clock=NOW, limit_seconds=LIMIT_SECONDS)
    assert stats.fresh_eligible == 5
    assert len(filtered.products) == 5
    assert filtered.source_updated_at == max(p.source_updated_at for p in products)


# B. 5 eligible / 3 fresh / 2 stale -> only 3 survive
def test_three_fresh_two_stale_filtered(fixed_limit):
    fresh_ts = [
        "2026-08-22T11:00:12+00:00",
        "2026-08-22T10:59:56+00:00",
        "2026-08-22T10:58:30+00:00",
    ]
    stale_ts = [
        "2026-08-22T09:26:46+00:00",
        "2026-08-22T08:29:00+00:00",
    ]
    products = [
        *[_live_product(f"f{i}", source_updated_at=ts) for i, ts in enumerate(fresh_ts)],
        *[_live_product(f"s{i}", source_updated_at=ts) for i, ts in enumerate(stale_ts)],
    ]
    filtered, stats = filter_fresh_products(_feed(*products), clock=NOW, limit_seconds=LIMIT_SECONDS)
    assert stats.eligible_before_freshness == 5
    assert stats.fresh_eligible == 3
    assert stats.stale_discarded == 2
    assert {p.external_id for p in filtered.products} == {"f0", "f1", "f2"}


# C. 2 fresh / 3 stale -> INSUFFICIENT_ADVERTISING_PRODUCTS
def test_two_fresh_three_stale_insufficient(fixed_limit):
    products = [
        _live_product("1", source_updated_at="2026-08-22T11:00:12+00:00"),
        _live_product("2", source_updated_at="2026-08-22T10:59:56+00:00"),
        _live_product("3", source_updated_at="2026-08-22T09:26:46+00:00"),
        _live_product("4", source_updated_at="2026-08-22T09:26:28+00:00"),
        _live_product("5", source_updated_at="2026-08-22T08:29:00+00:00"),
    ]

    class _Provider:
        name = "afrakala_public_bot_api"

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products)

    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products(provider=_Provider(), clock=NOW)
    assert exc.value.code == INSUFFICIENT_ADVERTISING_PRODUCTS
    assert exc.value.details["fresh_eligible"] == 2
    assert exc.value.details["stale_discarded"] == 3


# D. all eligible stale -> PRODUCT_FEED_STALE
def test_all_eligible_stale_raises(fixed_limit):
    products = tuple(
        _live_product(str(i), source_updated_at="2026-08-22T09:26:46+00:00")
        for i in range(5)
    )

    class _Provider:
        name = "afrakala_public_bot_api"

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products)

    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products(provider=_Provider(), clock=NOW)
    assert exc.value.code == PRODUCT_FEED_STALE
    assert exc.value.details["eligible_before_freshness"] == 5
    assert exc.value.details["fresh_eligible"] == 0


# E. stale product never appears in FrozenProductSnapshot
def test_stale_product_never_selected(fixed_limit):
    fresh = [
        _live_product("f1", source_updated_at="2026-08-22T11:00:12+00:00"),
        _live_product("f2", source_updated_at="2026-08-22T10:59:56+00:00"),
        _live_product("f3", source_updated_at="2026-08-22T10:58:30+00:00"),
    ]
    stale = _live_product("stale", source_updated_at="2026-08-22T08:29:00+00:00")
    filtered, _ = filter_fresh_products(_feed(*fresh, stale), clock=NOW, limit_seconds=LIMIT_SECONDS)
    composition = select_and_compose("سلام", filtered)
    assert composition.snapshot is not None
    selected_ids = {p.external_id for p in composition.snapshot.products}
    assert "stale" not in selected_ids
    assert selected_ids.issubset({"f1", "f2", "f3"})


# F. missing source_updated_at on live products -> stale discard
def test_missing_source_updated_at_live_fail_closed(fixed_limit):
    product = _live_product("1", source_updated_at=None)
    filtered, stats = filter_fresh_products(_feed(product), clock=NOW, limit_seconds=LIMIT_SECONDS)
    assert stats.fresh_eligible == 0
    assert stats.missing_timestamp_discarded == 1
    assert filtered.products == ()


# G. timezone-naive timestamp normalization
def test_timezone_naive_timestamp_is_fresh(fixed_limit):
    naive = datetime(2026, 8, 22, 11, 0, 12)  # noqa: DTZ001 — intentional naive fixture
    product = _live_product("1", source_updated_at=naive)
    filtered, stats = filter_fresh_products(_feed(product), clock=NOW, limit_seconds=LIMIT_SECONDS)
    assert stats.fresh_eligible == 1


# H. exactly age == threshold -> fresh
def test_exactly_at_threshold_is_fresh(fixed_limit):
    # NOW = 11:03:00, threshold 300s => reference 10:58:00 is exactly fresh
    product = _live_product("1", source_updated_at="2026-08-22T10:58:00+00:00")
    filtered, stats = filter_fresh_products(_feed(product), clock=NOW, limit_seconds=LIMIT_SECONDS)
    assert stats.fresh_eligible == 1


# I. age == threshold + 1 second -> stale
def test_threshold_plus_one_second_is_stale(fixed_limit):
    product = _live_product("1", source_updated_at="2026-08-22T10:57:59+00:00")
    filtered, stats = filter_fresh_products(_feed(product), clock=NOW, limit_seconds=LIMIT_SECONDS)
    assert stats.fresh_eligible == 0
    assert stats.stale_discarded == 1


# J. product_feed_status reports fresh/stale counts
def test_product_feed_status_reports_counts(fixed_limit):
    products = [
        _live_product("1", source_updated_at="2026-08-22T11:00:12+00:00"),
        _live_product("2", source_updated_at="2026-08-22T10:59:56+00:00"),
        _live_product("3", source_updated_at="2026-08-22T09:26:46+00:00"),
    ]

    class _Provider:
        name = "afrakala_public_bot_api"

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products, discarded_invalid=6)

    status = product_feed_status(provider=_Provider(), clock=NOW)
    assert status["advertising_eligible_count"] == 3
    assert status["fresh_eligible_count"] == 2
    assert status["stale_discarded"] == 1
    assert status["invalid_discarded"] == 6
    assert status["ok"] is False
    assert status["code"] == INSUFFICIENT_ADVERTISING_PRODUCTS


def test_product_feed_status_all_stale(fixed_limit):
    products = tuple(
        _live_product(str(i), source_updated_at="2026-08-22T09:26:46+00:00")
        for i in range(3)
    )

    class _Provider:
        name = "afrakala_public_bot_api"

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products)

    status = product_feed_status(provider=_Provider(), clock=NOW)
    assert status["ok"] is False
    assert status["code"] == PRODUCT_FEED_STALE
    assert status["fresh_eligible_count"] == 0
    assert status["advertising_eligible_count"] == 3


# K. fake/legacy provider without timestamps remains compatible
def test_fake_provider_without_timestamps_still_fresh(fixed_limit):
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


def test_live_evidence_none_fresh_at_1111(fixed_limit):
    """Model host observation: at 11:11:11 none of the sample timestamps are fresh."""
    clock = datetime(2026, 8, 22, 11, 11, 11, tzinfo=timezone.utc)
    timestamps = [
        "2026-08-22T11:00:12+00:00",
        "2026-08-22T10:59:56+00:00",
        "2026-08-22T09:26:46+00:00",
        "2026-08-22T09:26:28+00:00",
        "2026-08-22T08:29:00+00:00",
    ]
    products = [_live_product(str(i), source_updated_at=ts) for i, ts in enumerate(timestamps)]

    class _Provider:
        name = "afrakala_public_bot_api"

        def fetch_advertising_products(self, *, clock=None):
            return _feed(*products)

    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products(provider=_Provider(), clock=clock)
    assert exc.value.code == PRODUCT_FEED_STALE
