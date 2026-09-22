"""Send-time AfraKala refresh. Prepare identity is not the outbound price."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core_engine.config import get_settings
from core_engine.services.product_feed.errors import PRODUCT_FEED_UNAVAILABLE
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.send_time_refresh import (
    CASH_PREPAYMENT_PRICE_MISSING_AT_SEND,
    PRODUCT_NOT_ADVERTISING_AT_SEND,
    PRODUCT_REFRESH_FAILED,
    PRODUCT_UNAVAILABLE_AT_SEND,
    SEND_TIME_PRICE_NOTICE,
    SendTimeRefreshCycle,
    apply_send_time_product_refresh,
    external_send_may_have_accepted,
    render_outbound_from_selection,
)
from core_engine.services.product_feed.service import set_product_feed_provider


def _price(amount, *, settlement="cash"):
    return {
        "sale_price_type_title": "نقدی" if settlement == "cash" else "سایر",
        "settlement_type_code": settlement,
        "final_sale_price": amount,
        "rounded_sale_price": amount + 1,
        "computed_at": "2026-08-22T10:00:00+00:00",
    }


def _row(product_id, *, tags, available, cash, other=None, name="محصول الف"):
    prices = []
    if cash is not None:
        prices.append(_price(cash))
    if other is not None:
        prices.append(_price(other, settlement="three_day"))
    payload = {
        "id": product_id,
        "sku": product_id,
        "name": name,
        "status": "active",
        "labels": [{"title": tag} for tag in tags],
        "prices": prices,
    }
    if available is True:
        payload["stock_status"] = "available"
    elif available is False:
        payload["stock_status"] = "out_of_stock"
    return payload


def _payload(product_ids, prose="سلام", heading="محصولات ویژه امروز:"):
    return {
        "message_id": 1,
        "campaign_id": 9,
        "contact_id": 3,
        "account_id": 2,
        "platform": "rubika",
        "recipient": "+989121234567",
        "recipient_type": "user",
        "message_text": prose,
        "dedupe_key": "d1",
        "attempt": 1,
        "metadata": {
            "include_products": True,
            "selected_product_ids": list(product_ids),
            "prose_text": prose,
            "product_heading": heading,
            "price_authoritative": False,
        },
    }


def test_prepare_price_is_not_outbound_price():
    later = FakeProductFeedProvider([
        _row("A", tags=["تبلیغات"], available=True, cash=11_000_000),
    ])
    result = render_outbound_from_selection(
        _payload(["A"]),
        provider=later,
    )
    assert result.blocked is False
    assert result.message_text is not None
    assert "۱۱,۰۰۰,۰۰۰" in result.message_text
    assert "۱۰,۰۰۰,۰۰۰" not in result.message_text
    assert result.audit is not None
    assert result.audit["products"][0]["cash_prepayment_price"] == "11000000"
    assert result.audit["price_authoritative_for_next_send"] is False


def test_unavailable_at_send_blocks_without_stale_price():
    provider = FakeProductFeedProvider([
        _row("A", tags=["تبلیغات"], available=False, cash=11_000_000),
    ])
    result = render_outbound_from_selection(_payload(["A"]), provider=provider)
    assert result.blocked is True
    assert result.reason_code == PRODUCT_UNAVAILABLE_AT_SEND
    assert result.message_text is None
    assert result.audit["products"][0]["sent"] is False


def test_tag_removed_at_send_blocks():
    provider = FakeProductFeedProvider([
        _row("A", tags=["ویژه"], available=True, cash=11_000_000),
    ])
    result = render_outbound_from_selection(_payload(["A"]), provider=provider)
    assert result.blocked is True
    assert result.reason_code == PRODUCT_NOT_ADVERTISING_AT_SEND


def test_missing_cash_price_does_not_use_three_day_price():
    provider = FakeProductFeedProvider([
        _row("A", tags=["تبلیغات"], available=True, cash=None, other=8_000_000),
    ])
    result = render_outbound_from_selection(_payload(["A"]), provider=provider)
    assert result.blocked is True
    assert result.reason_code == CASH_PREPAYMENT_PRICE_MISSING_AT_SEND
    assert result.message_text is None
    assert "۸,۰۰۰,۰۰۰" not in str(result.audit)


def test_retry_before_external_accept_refreshes_again():
    first = FakeProductFeedProvider([
        _row("A", tags=["تبلیغات"], available=True, cash=11_000_000),
    ])
    prepared = render_outbound_from_selection(_payload(["A"]), provider=first)
    assert prepared.blocked is False
    retry_payload = dict(prepared.payload)
    retry_payload["attempt"] = 2
    retry_payload["metadata"] = dict(prepared.payload["metadata"])
    second = FakeProductFeedProvider([
        _row("A", tags=["تبلیغات"], available=True, cash=12_000_000),
    ])
    retried = render_outbound_from_selection(retry_payload, provider=second)
    assert retried.blocked is False
    assert retried.message_text is not None
    assert "۱۲,۰۰۰,۰۰۰" in retried.message_text
    assert "۱۱,۰۰۰,۰۰۰" not in retried.message_text
    assert second.call_count == 1


def test_submitted_attempt_does_not_create_a_second_price():
    submitted = _payload(["A"])
    submitted["message_text"] = "سلام\n\nمحصول الف\nقیمت نقدی (پیش واریز): ۱۱,۰۰۰,۰۰۰ ریال"
    submitted["metadata"]["external_send_submitted"] = True
    submitted["metadata"]["send_time_product_audit"] = {
        "final_text": submitted["message_text"],
        "products": [{"source_product_id": "A", "cash_prepayment_price": "11000000"}],
    }
    later = FakeProductFeedProvider([
        _row("A", tags=["تبلیغات"], available=True, cash=12_000_000),
    ])
    result = render_outbound_from_selection(submitted, provider=later)
    assert external_send_may_have_accepted(submitted) is True
    assert result.blocked is False
    assert result.refreshed is False
    assert result.message_text == submitted["message_text"]
    assert later.call_count == 0


def test_audit_records_price_actually_used():
    provider = FakeProductFeedProvider([
        _row("A", tags=[" تبلیغات "], available=True, cash=11_000_000, name="محصول الف"),
    ])
    result = render_outbound_from_selection(_payload(["A"]), provider=provider)
    product = result.audit["products"][0]
    assert product["source_product_id"] == "A"
    assert product["product_name"] == "محصول الف"
    assert product["cash_prepayment_price"] == "11000000"
    assert product["availability"] == "available"
    assert product["advertising_tag_valid"] is True
    assert result.audit["afrakala_refreshed_at"]
    assert result.audit["rendered_at"]
    assert result.audit["final_text"] == result.message_text


def test_preview_notice_is_not_an_outbound_price():
    assert SEND_TIME_PRICE_NOTICE == "قیمت محصول هنگام ارسال مجدداً از افراکالا بررسی می‌شود."
    preview_price = "10000000"
    provider = FakeProductFeedProvider([
        _row("A", tags=["تبلیغات"], available=True, cash=11_000_000),
    ])
    outbound = render_outbound_from_selection(_payload(["A"]), provider=provider)
    assert preview_price not in (outbound.message_text or "")
    assert outbound.audit["products"][0]["cash_prepayment_price"] != preview_price


def test_campaign_without_products_is_unchanged():
    payload = _payload([])
    payload["metadata"] = {"include_products": False}
    payload["message_text"] = "فقط سلام"
    provider = FakeProductFeedProvider([])
    result = apply_send_time_product_refresh(payload, provider=provider)
    assert result.blocked is False
    assert result.refreshed is False
    assert result.message_text == "فقط سلام"
    assert provider.call_count == 0


def test_feed_failure_does_not_fabricate_or_write():
    provider = FakeProductFeedProvider(fail_code=PRODUCT_FEED_UNAVAILABLE)
    result = render_outbound_from_selection(_payload(["A"]), provider=provider)
    assert result.blocked is True
    assert result.reason_code == PRODUCT_REFRESH_FAILED
    assert result.message_text is None
    assert not hasattr(provider, "create_product")
    assert not hasattr(provider, "update_price")


def test_dispatch_cycle_shares_one_fresh_fetch_and_refetches_after_existing_limit():
    limit = int(get_settings().AFRAKALA_PRODUCT_MAX_STALENESS_SECONDS)
    assert limit == 300
    provider = FakeProductFeedProvider([
        _row("A", tags=["تبلیغات"], available=True, cash=11_000_000),
        _row("E", tags=["تبلیغات"], available=True, cash=6_000_000, name="محصول ه"),
    ])
    start = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    cycle = SendTimeRefreshCycle(max_age_seconds=limit)
    first = render_outbound_from_selection(
        _payload(["A"]),
        provider=provider,
        cycle=cycle,
        clock=start,
    )
    second = render_outbound_from_selection(
        _payload(["E"]),
        provider=provider,
        cycle=cycle,
        clock=start + timedelta(seconds=30),
    )
    assert first.blocked is False and second.blocked is False
    assert provider.call_count == 1
    provider.rows = [
        _row("A", tags=["تبلیغات"], available=True, cash=13_000_000),
    ]
    stale = render_outbound_from_selection(
        _payload(["A"]),
        provider=provider,
        cycle=cycle,
        clock=start + timedelta(seconds=limit + 1),
    )
    assert provider.call_count == 2
    assert stale.blocked is False
    assert stale.audit["products"][0]["cash_prepayment_price"] == "13000000"


def test_refresh_does_not_replace_a_missing_product():
    provider = FakeProductFeedProvider([
        _row("E", tags=["تبلیغات"], available=True, cash=6_000_000, name="محصول ه"),
    ])
    result = render_outbound_from_selection(_payload(["A"]), provider=provider)
    assert result.blocked is True
    assert result.reason_code == PRODUCT_REFRESH_FAILED
    assert result.message_text is None
