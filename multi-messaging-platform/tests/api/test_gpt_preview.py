"""Phase 5.6 GPT preview API."""

from core_engine.services.message_variation.fake_provider import FakeMessageVariationProvider
from core_engine.services.message_variation.service import (
    reset_preview_rate_guard,
    set_message_variation_provider,
)
from core_engine.services.product_feed.errors import PRODUCT_FEED_UNAVAILABLE
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.service import set_product_feed_provider

AUTH_HEADERS = {"Authorization": "Bearer fake_token"}
TEMPLATE = "سلام {{first_name}}، برای اطلاع از قیمت‌های امروز با ما در ارتباط باشید."


def _variations():
    return [
        "سلام {{first_name}} عزیز، نسخه ۱ اطلاع‌رسانی امروز آماده است.",
        "سلام {{first_name}} عزیز، نسخه ۲ اطلاع‌رسانی امروز آماده است.",
        "سلام {{first_name}} عزیز، نسخه ۳ اطلاع‌رسانی امروز آماده است.",
    ]


def test_gpt_status_config_pending(client, admin_auth):
    set_message_variation_provider(None)
    response = client.get("/campaigns/gpt-status", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["live_binding"] == "CONFIG_PENDING"
    assert "key" not in str(body).lower()
    assert "token" not in str(body).lower()


def test_gpt_preview_valid(client, admin_auth):
    reset_preview_rate_guard()
    set_message_variation_provider(FakeMessageVariationProvider(_variations()))
    try:
        response = client.post(
            "/campaigns/gpt-preview",
            headers=AUTH_HEADERS,
            json={"template_text": TEMPLATE, "include_products": False},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert len(body["samples"]) == 3
        assert "{{first_name}}" in body["samples"][0]["prose_text"]
    finally:
        set_message_variation_provider(None)


def test_gpt_preview_rejects_client_secrets(client, admin_auth):
    reset_preview_rate_guard()
    response = client.post(
        "/campaigns/gpt-preview",
        headers=AUTH_HEADERS,
        json={"template_text": TEMPLATE, "openai_api_key": "sk-secret"},
    )
    assert response.status_code == 422


def test_gpt_preview_unavailable(client, admin_auth):
    reset_preview_rate_guard()
    set_message_variation_provider(None)
    response = client.post(
        "/campaigns/gpt-preview",
        headers=AUTH_HEADERS,
        json={"template_text": TEMPLATE},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] in {"GPT_NOT_CONFIGURED", "GPT_UNAVAILABLE"}
    assert "traceback" not in str(body).lower()


def test_gpt_preview_with_products(client, admin_auth):
    reset_preview_rate_guard()
    set_message_variation_provider(FakeMessageVariationProvider(_variations()))
    rows = [
        {
            "sku": f"S{i}",
            "title": f"کالا {i}",
            "cash_price": 1000 + i,
            "currency": "IRR",
            "advertising": True,
        }
        for i in range(5)
    ]
    set_product_feed_provider(FakeProductFeedProvider(rows))
    try:
        response = client.post(
            "/campaigns/gpt-preview",
            headers=AUTH_HEADERS,
            json={"template_text": TEMPLATE, "include_products": True},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        sample = body["samples"][0]
        assert sample["immutable_product_block"]
        assert sample["final_text"].endswith(sample["immutable_product_block"])
        assert "نمونه پیش‌نمایش" in (body.get("product_preview_note") or "")
    finally:
        set_message_variation_provider(None)
        set_product_feed_provider(None)


def test_gpt_preview_product_feed_unavailable(client, admin_auth):
    reset_preview_rate_guard()
    set_message_variation_provider(FakeMessageVariationProvider(_variations()))
    set_product_feed_provider(FakeProductFeedProvider(fail_code=PRODUCT_FEED_UNAVAILABLE))
    try:
        response = client.post(
            "/campaigns/gpt-preview",
            headers=AUTH_HEADERS,
            json={"template_text": TEMPLATE, "include_products": True},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["product_error"]["code"] == PRODUCT_FEED_UNAVAILABLE
        assert body["samples"]
    finally:
        set_message_variation_provider(None)
        set_product_feed_provider(None)
