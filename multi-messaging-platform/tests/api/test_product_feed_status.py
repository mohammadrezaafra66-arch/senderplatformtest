"""Phase 5.5 product-feed status API."""

from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.service import set_product_feed_provider

AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


def test_product_feed_status_config_pending(client, admin_auth):
    set_product_feed_provider(None)
    response = client.get("/campaigns/product-feed/status", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["code"] in {"CONFIG_PENDING", "PRODUCT_FEED_UNAVAILABLE"}
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
