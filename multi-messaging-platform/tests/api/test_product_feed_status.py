"""Phase 5.5 product-feed status API."""

import httpx

from core_engine.config import get_settings
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.service import set_product_feed_provider

AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


def test_product_feed_status_config_pending(client, admin_auth, monkeypatch):
    # Host environments may configure a real AfraKala URL/token in .env.
    # Force CONFIG_PENDING so this test never performs a live feed read.
    monkeypatch.setenv("AFRAKALA_PRODUCT_API_BASE_URL", "")
    monkeypatch.setenv("AFRAKALA_PRODUCT_API_TOKEN", "")
    get_settings.cache_clear()

    class _ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "Live AfraKala HTTP client must not be used in CONFIG_PENDING test"
            )

    monkeypatch.setattr(httpx, "Client", _ForbiddenClient)

    set_product_feed_provider(None)
    response = client.get("/campaigns/product-feed/status", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "CONFIG_PENDING"
    assert body["live_binding"] == "CONFIG_PENDING"
    assert "token" not in str(body).lower()
    assert "secret" not in str(body).lower()


def test_product_feed_status_with_fake(client, admin_auth):
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
    set_product_feed_provider(FakeProductFeedProvider(rows))
    try:
        response = client.get("/campaigns/product-feed/status", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["eligible_count"] == 5
    finally:
        set_product_feed_provider(None)
