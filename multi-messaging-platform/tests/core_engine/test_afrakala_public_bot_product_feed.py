"""Phase 12 — AfraKala public bot product feed (hermetic, no live network)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from core_engine.services.product_feed.afrakala_adapter import (
    SOURCE_PUBLIC_BOT_API,
    has_explicit_advertising_label,
    normalize_public_bot_product,
    select_cash_price,
)
from core_engine.services.product_feed.afrakala_public_bot_provider import (
    AfraKalaPublicBotProductFeedProvider,
)
from core_engine.services.product_feed.canonical import (
    canonicalize_product_row,
    is_explicitly_advertising,
)
from core_engine.services.product_feed.errors import (
    CONFIG_PENDING,
    INSUFFICIENT_ADVERTISING_PRODUCTS,
    PRODUCT_FEED_INVALID_RESPONSE,
    PRODUCT_FEED_TIMEOUT,
    PRODUCT_FEED_UNAVAILABLE,
    ProductFeedError,
)
from core_engine.services.product_feed.service import fetch_current_advertising_products

# Fixed timeline for hermetic freshness tests (fixture data at 10:00Z).
FIXTURE_SOURCE_TS = "2026-08-22T10:00:00+00:00"
FRESH_CLOCK = datetime(2026, 8, 22, 10, 1, 0, tzinfo=timezone.utc)
STALE_REFERENCE_TS = "2026-08-22T08:00:00+00:00"
STALE_CHECK_CLOCK = datetime(2026, 8, 22, 10, 1, 0, tzinfo=timezone.utc)


def _cash_price(
    *,
    amount: int,
    computed_at: str,
    price_type: str = "نقدی",
    final: int | None = None,
    rounded: int | None = None,
) -> dict:
    return {
        "sale_price_type_title": price_type,
        "final_sale_price": final if final is not None else amount,
        "rounded_sale_price": rounded if rounded is not None else amount,
        "computed_at": computed_at,
    }


def _product(
    *,
    product_id: int | str,
    sku: str = "SKU-1",
    name: str = "تلویزیون سامسونگ",
    labels: list | None = None,
    prices: list | None = None,
    stock_status: str = "available",
    updated_at: str = FIXTURE_SOURCE_TS,
) -> dict:
    return {
        "id": product_id,
        "sku": sku,
        "name": name,
        "brand": "Samsung",
        "category": "TV",
        "stock_status": stock_status,
        "labels": labels
        if labels is not None
        else [{"title": "تبلیغات"}, {"title": "پیشنهاد"}],
        "prices": prices
        if prices is not None
        else [_cash_price(amount=28_500_000, computed_at=FIXTURE_SOURCE_TS)],
        "updated_at": updated_at,
    }


def _page_payload(products: list[dict], *, has_more: bool = False) -> dict:
    return {"products": products, "pagination": {"has_more": has_more}}


# ── adapter / labels ──────────────────────────────────────────────


def test_advertising_label_present():
    raw = _product(product_id=1)
    assert has_explicit_advertising_label(raw["labels"]) is True
    flat, reason = normalize_public_bot_product(raw, default_currency="IRR")
    assert reason is None
    assert flat is not None
    assert flat["advertising"] is True


def test_advertising_label_absent():
    raw = _product(product_id=2, labels=[{"title": "پیشنهاد"}])
    assert has_explicit_advertising_label(raw["labels"]) is False
    flat, reason = normalize_public_bot_product(raw, default_currency="IRR")
    assert flat is None
    assert reason == "not_advertising"


def test_nested_labels_with_whitespace():
    raw = _product(product_id=3, labels=[{"title": "  تبلیغات  "}])
    assert has_explicit_advertising_label(raw["labels"]) is True
    assert is_explicitly_advertising({"labels": [{"title": "  تبلیغات  "}]}) is True


def test_only_site_price_rejected():
    raw = _product(
        product_id=4,
        prices=[_cash_price(amount=1, computed_at="2026-08-22T10:00:00+00:00", price_type="سایت")],
    )
    flat, reason = normalize_public_bot_product(raw, default_currency="IRR")
    assert flat is None
    assert reason == "missing_cash_price"


def test_multiple_cash_prices_select_newest():
    prices = [
        _cash_price(amount=10_000_000, computed_at="2026-08-22T08:00:00+00:00"),
        _cash_price(amount=11_000_000, computed_at="2026-08-22T12:00:00+00:00"),
        _cash_price(amount=9_000_000, computed_at="2026-08-22T06:00:00+00:00"),
    ]
    selected = select_cash_price(prices)
    assert selected is not None
    price, computed_at = selected
    assert price == Decimal("11000000")
    assert computed_at == datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def test_prefers_final_sale_price_over_rounded():
    record = {
        "sale_price_type_title": "نقدی",
        "final_sale_price": 28_500_000,
        "rounded_sale_price": 28_000_000,
        "computed_at": "2026-08-22T10:00:00+00:00",
    }
    selected = select_cash_price([record])
    assert selected is not None
    assert selected[0] == Decimal("28500000")


def test_malformed_price_rejected():
    raw = _product(
        product_id=5,
        prices=[
            {
                "sale_price_type_title": "نقدی",
                "final_sale_price": 12.5,
                "computed_at": "2026-08-22T10:00:00+00:00",
            }
        ],
    )
    flat, reason = normalize_public_bot_product(raw, default_currency="IRR")
    assert flat is None
    assert reason == "missing_cash_price"


def test_unavailable_stock_discarded():
    raw = _product(product_id=6, stock_status="out_of_stock")
    flat, reason = normalize_public_bot_product(raw, default_currency="IRR")
    assert flat is None
    assert reason == "unavailable_stock"


def test_available_stock_retained():
    raw = _product(product_id=7, stock_status="available")
    flat, reason = normalize_public_bot_product(raw, default_currency="IRR")
    assert reason is None
    assert flat is not None


def test_canonical_maps_eligible_product_fields():
    raw = _product(product_id=99, sku="TV-X", name="تلویزیون X")
    flat, _ = normalize_public_bot_product(raw, default_currency="IRR")
    product, reason = canonicalize_product_row(
        flat,
        default_currency="IRR",
        default_source=SOURCE_PUBLIC_BOT_API,
        fetched_at=FRESH_CLOCK,
    )
    assert reason is None
    assert product is not None
    assert product.external_id == "99"
    assert product.product_code == "TV-X"
    assert product.name == "تلویزیون X"
    assert product.price == Decimal("28500000")
    assert product.currency == "IRR"
    assert product.advertising is True
    assert product.source == SOURCE_PUBLIC_BOT_API
    assert product.source_updated_at is not None


# ── provider / HTTP ───────────────────────────────────────────────


class _MockResponse:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None, body: bytes | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = body if body is not None else json.dumps(self._payload).encode("utf-8")

    def json(self):
        if isinstance(self.content, bytes) and self.content == b"not-json":
            raise json.JSONDecodeError("expecting value", "doc", 0)
        return self._payload


def _install_client(monkeypatch, handler):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            return handler(url, headers or {})

    monkeypatch.setattr(httpx, "Client", _Client)


def test_empty_base_url_config_pending():
    provider = AfraKalaPublicBotProductFeedProvider(base_url="")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == CONFIG_PENDING


def test_pagination_across_multiple_pages(monkeypatch):
    calls: list[str] = []

    def handler(url, headers):
        calls.append(url)
        assert headers.get("Authorization") == "Bearer secret-token"
        if "page=1" in url:
            return _MockResponse(
                payload=_page_payload([_product(product_id=1)], has_more=True)
            )
        if "page=2" in url:
            return _MockResponse(
                payload=_page_payload([_product(product_id=2, sku="SKU-2")], has_more=False)
            )
        raise AssertionError(f"unexpected url {url}")

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(
        base_url="https://afrakala.example",
        token="secret-token",
        page_size=100,
    )
    result = provider.fetch_advertising_products(clock=FRESH_CLOCK)
    assert len(result.products) == 2
    assert calls[0].endswith("/api/public/bot/products?page=1&page_size=100")
    assert calls[1].endswith("/api/public/bot/products?page=2&page_size=100")


def test_duplicate_product_id_deduped(monkeypatch):
    same = _product(product_id=42)

    def handler(url, headers):
        if "page=1" in url:
            return _MockResponse(payload=_page_payload([same], has_more=True))
        if "page=2" in url:
            return _MockResponse(payload=_page_payload([same], has_more=False))
        raise AssertionError(url)

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    result = provider.fetch_advertising_products(clock=FRESH_CLOCK)
    assert len(result.products) == 1


def test_no_advertising_products_eligible(monkeypatch):
    def handler(url, headers):
        return _MockResponse(
            payload=_page_payload(
                [_product(product_id=1, labels=[{"title": "پیشنهاد"}])],
                has_more=False,
            )
        )

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    result = provider.fetch_advertising_products(clock=FRESH_CLOCK)
    assert result.products == ()
    assert result.discarded_invalid >= 1
    assert "not_advertising" in result.diagnostics


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_errors_fail_closed(monkeypatch, status_code):
    def handler(url, headers):
        return _MockResponse(status_code=status_code, payload={})

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(
        base_url="https://afrakala.example",
        token="secret-token",
    )
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_UNAVAILABLE


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_rate_limit_and_server_errors(monkeypatch, status_code):
    def handler(url, headers):
        return _MockResponse(status_code=status_code, payload={})

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_UNAVAILABLE


def test_timeout(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "Client", _Client)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_TIMEOUT


def test_malformed_json(monkeypatch):
    def handler(url, headers):
        return _MockResponse(status_code=200, body=b"not-json")

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_INVALID_RESPONSE


def test_malformed_pagination(monkeypatch):
    def handler(url, headers):
        return _MockResponse(payload={"products": [], "pagination": {"has_more": "yes"}})

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_INVALID_RESPONSE


def test_token_not_logged(caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    def handler(url, headers):
        assert "secret-live-token" in headers.get("Authorization", "")
        return _MockResponse(payload=_page_payload([], has_more=False))

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(
        base_url="https://afrakala.example",
        token="secret-live-token",
    )
    provider.fetch_advertising_products(clock=FRESH_CLOCK)
    combined = caplog.text.lower()
    assert "secret-live-token" not in combined
    assert "authorization: bearer" not in combined


def test_old_computed_at_remains_eligible_after_live_fetch(monkeypatch):
    rows = [
        _product(
            product_id=i,
            sku=f"S{i}",
            updated_at=STALE_REFERENCE_TS,
            prices=[_cash_price(amount=10_000_000 + i, computed_at=STALE_REFERENCE_TS)],
        )
        for i in range(1, 4)
    ]

    def handler(url, headers):
        return _MockResponse(payload=_page_payload(rows, has_more=False))

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    monkeypatch.setattr(
        "core_engine.services.product_feed.service._max_staleness_seconds",
        lambda: 60,
    )
    result = fetch_current_advertising_products(
        provider=provider,
        clock=STALE_CHECK_CLOCK,
    )
    assert len(result.products) == 3
    assert all(p.source == SOURCE_PUBLIC_BOT_API for p in result.products)


def test_fetch_minimum_three_eligible(monkeypatch):
    rows = [_product(product_id=i, sku=f"S{i}") for i in range(1, 4)]

    def handler(url, headers):
        return _MockResponse(payload=_page_payload(rows, has_more=False))

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    result = fetch_current_advertising_products(provider=provider, clock=FRESH_CLOCK)
    assert len(result.products) == 3
    assert all(p.advertising for p in result.products)
    assert all(p.source == SOURCE_PUBLIC_BOT_API for p in result.products)


def test_insufficient_eligible_raises(monkeypatch):
    rows = [_product(product_id=1), _product(product_id=2, sku="S2")]

    def handler(url, headers):
        return _MockResponse(payload=_page_payload(rows, has_more=False))

    _install_client(monkeypatch, handler)
    provider = AfraKalaPublicBotProductFeedProvider(base_url="https://afrakala.example")
    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products(provider=provider, clock=FRESH_CLOCK)
    assert exc.value.code == INSUFFICIENT_ADVERTISING_PRODUCTS
