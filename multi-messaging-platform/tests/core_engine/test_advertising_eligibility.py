"""New Phase 3 — advertising eligibility contract."""

from __future__ import annotations

from decimal import Decimal

from core_engine.services.product_feed.advertising_eligibility import (
    ADVERTISING_TAG,
    CASH_PREPAYMENT_PRICE_MISSING,
    INVALID_PRICE,
    MISSING_ADVERTISING_TAG,
    PRODUCT_UNAVAILABLE,
    evaluate_advertising_product,
    filter_eligible_products,
)
from core_engine.services.product_feed.composition import compose_campaign_message
from core_engine.services.product_feed.selection import select_explicit_products


def _price(amount: int, *, code: str = "cash_price", settlement: str = "cash") -> dict:
    return {
        "sale_price_type_title": "نقدی",
        "sale_price_type_code": code,
        "settlement_type_code": settlement,
        "current_price": amount,
        "rounded_sale_price": amount + 1,
        "final_sale_price": amount + 2,
        "computed_at": "2026-08-22T10:00:00+00:00",
    }


def _row(
    *,
    product_id: str,
    name: str,
    tags: list,
    available: bool | None,
    cash: int | None,
    other_price: int | None = None,
    brand: str = "سامسونگ",
    category: str = "تلویزیون",
    status: str = "active",
) -> dict:
    prices = []
    if cash is not None:
        prices.append(_price(cash))
    if other_price is not None:
        prices.append(_price(other_price, code="cash_price", settlement="three_day"))
    stock = "available" if available is True else "out_of_stock" if available is False else None
    row = {
        "id": product_id,
        "sku": product_id,
        "name": name,
        "brand": brand,
        "category": category,
        "status": status,
        "labels": [{"title": tag} for tag in tags],
        "prices": prices,
    }
    if stock is not None:
        row["stock_status"] = stock
    return row


def test_brand_and_category_objects_use_name():
    row = _row(product_id="obj", name="محصول", tags=[ADVERTISING_TAG], available=True, cash=4_000_000)
    row["brand"] = {"name": "بوش"}
    row["category"] = {"name": "لوازم خانگی"}
    result = evaluate_advertising_product(row)
    assert result.eligible is True
    assert result.brand == "بوش"
    assert result.category == "لوازم خانگی"


def test_exact_advertising_tag_and_whitespace():
    exact = evaluate_advertising_product(_row(product_id="a", name="A", tags=[ADVERTISING_TAG], available=True, cash=10))
    spaced = evaluate_advertising_product(
        _row(product_id="a", name="A", tags=[" تبلیغات "], available=True, cash=10)
    )
    assert exact.eligible is True
    assert spaced.eligible is True
    assert MISSING_ADVERTISING_TAG not in spaced.reason_codes


def test_similar_tags_do_not_match():
    for tag in ("تبلیغ", "تبلیغاتی", "تبلیغات ویژه", "Advertising", "ads"):
        result = evaluate_advertising_product(
            _row(product_id="x", name="X", tags=[tag], available=True, cash=10)
        )
        assert result.eligible is False
        assert MISSING_ADVERTISING_TAG in result.reason_codes


def test_advertising_tag_among_other_tags_passes_tag_gate():
    result = evaluate_advertising_product(
        _row(product_id="e", name="E", tags=["ویژه", " تبلیغات "], available=True, cash=6_000_000)
    )
    assert result.eligible is True
    assert result.cash_prepayment_price == Decimal("6000000")


def test_unavailable_and_unknown_stock_fail_closed():
    unavailable = evaluate_advertising_product(
        _row(product_id="b", name="B", tags=[ADVERTISING_TAG], available=False, cash=9_000_000)
    )
    unknown = evaluate_advertising_product(
        _row(product_id="u", name="U", tags=[ADVERTISING_TAG], available=None, cash=9_000_000)
    )
    assert unavailable.eligible is False
    assert PRODUCT_UNAVAILABLE in unavailable.reason_codes
    assert unknown.availability == "unknown"
    assert unknown.eligible is False
    assert PRODUCT_UNAVAILABLE in unknown.reason_codes


def test_only_cash_prepayment_price_is_eligible_and_rendered():
    missing = evaluate_advertising_product(
        _row(product_id="c", name="C", tags=[ADVERTISING_TAG], available=True, cash=None, other_price=8_000_000)
    )
    zero = evaluate_advertising_product(
        _row(product_id="z", name="Z", tags=[ADVERTISING_TAG], available=True, cash=0)
    )
    eligible = evaluate_advertising_product(
        _row(product_id="a", name="محصول الف", tags=[ADVERTISING_TAG], available=True, cash=10_000_000)
    )
    assert missing.eligible is False
    assert CASH_PREPAYMENT_PRICE_MISSING in missing.reason_codes
    assert missing.cash_prepayment_price is None
    assert zero.eligible is False
    assert INVALID_PRICE in zero.reason_codes
    assert eligible.eligible is True
    assert eligible.product is not None
    composition = compose_campaign_message(
        "سلام",
        [eligible.product],
        heading="محصولات ویژه امروز:",
        unit_label="ریال",
    )
    assert "محصول الف" in composition.final_text
    assert "قیمت نقدی (پیش واریز)" in composition.final_text
    assert "8000000" not in composition.final_text
    assert "None" not in composition.final_text
    assert composition.snapshot is not None
    assert composition.snapshot.products[0].price == "10000000"


def test_combined_gates_and_filters():
    rows = [
        _row(product_id="a", name="A", tags=[ADVERTISING_TAG], available=True, cash=10, brand="سامسونگ", category="موبایل"),
        _row(product_id="b", name="B", tags=[ADVERTISING_TAG], available=False, cash=9, brand="سامسونگ", category="موبایل"),
        _row(product_id="c", name="C", tags=[ADVERTISING_TAG], available=True, cash=None, brand="سامسونگ", category="موبایل"),
        _row(product_id="d", name="D", tags=["تبلیغاتی"], available=True, cash=7, brand="ال جی", category="تلویزیون"),
        _row(product_id="e", name="E", tags=["ویژه", " تبلیغات "], available=True, cash=6, brand="سامسونگ", category="تلویزیون"),
    ]
    decisions = [evaluate_advertising_product(row) for row in rows]
    eligible = filter_eligible_products(decisions)
    assert {item.product.external_id for item in eligible if item.product} == {"a", "e"}
    narrowed = filter_eligible_products(decisions, brand="سامسونگ", category="تلویزیون")
    assert [item.product.external_id for item in narrowed if item.product] == ["e"]
    selected = select_explicit_products(
        [item.product for item in eligible if item.product],
        ["a", "e"],
    )
    assert [item.external_id for item in selected] == ["a", "e"]
    try:
        select_explicit_products(
            [item.product for item in eligible if item.product],
            ["b"],
        )
    except Exception as exc:
        assert getattr(exc, "details", {}).get("missing_or_ineligible") == ["b"]
    else:
        raise AssertionError("ineligible id was selectable")


def test_feed_failure_does_not_fabricate_products():
    from core_engine.services.product_feed.errors import PRODUCT_FEED_UNAVAILABLE, ProductFeedError
    from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider

    provider = FakeProductFeedProvider(fail_code=PRODUCT_FEED_UNAVAILABLE)
    try:
        provider.fetch_advertising_products()
    except ProductFeedError as exc:
        assert exc.code == PRODUCT_FEED_UNAVAILABLE
    else:
        raise AssertionError("unavailable feed fabricated a result")


def test_prepare_snapshot_is_not_the_next_outbound_price():
    """Historical reconstruction may replay an old audit. Outbound must not."""
    first = evaluate_advertising_product(
        _row(product_id="a", name="محصول الف", tags=[ADVERTISING_TAG], available=True, cash=10_000_000)
    )
    assert first.product is not None
    prepared = compose_campaign_message("سلام", [first.product], heading="محصولات ویژه امروز:", unit_label="ریال")
    later = evaluate_advertising_product(
        _row(product_id="a", name="محصول الف", tags=[ADVERTISING_TAG], available=True, cash=11_000_000)
    )
    assert later.product is not None
    outbound = compose_campaign_message(
        "سلام",
        [later.product],
        heading="محصولات ویژه امروز:",
        unit_label="ریال",
    )
    assert "۱۱,۰۰۰,۰۰۰" in outbound.final_text
    assert "۱۰,۰۰۰,۰۰۰" not in outbound.final_text
    assert prepared.snapshot is not None
    assert prepared.snapshot.products[0].price == "10000000"
    assert outbound.snapshot is not None
    assert outbound.snapshot.products[0].price == "11000000"
