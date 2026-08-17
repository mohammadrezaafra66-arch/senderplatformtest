"""Phase 5.5 — AfraKala advertising product feed (no live network)."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.product_feed.canonical import canonicalize_product_row, is_explicitly_advertising
from core_engine.services.product_feed.composition import (
    compose_campaign_message,
    compose_with_locked_products,
    format_price_display,
)
from core_engine.services.product_feed.errors import (
    CONFIG_PENDING,
    INSUFFICIENT_ADVERTISING_PRODUCTS,
    PRODUCT_FEED_EMPTY,
    PRODUCT_FEED_INVALID_RESPONSE,
    PRODUCT_FEED_STALE,
    PRODUCT_FEED_TIMEOUT,
    PRODUCT_FEED_UNAVAILABLE,
    ProductFeedError,
)
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.http_provider import HttpJsonProductFeedProvider
from core_engine.services.product_feed.selection import select_advertising_products
from core_engine.services.product_feed.service import (
    fetch_current_advertising_products,
    select_and_compose,
    set_product_feed_provider,
)
from workers.payload_adapter import normalize_queue_payload
from workers.payloads import WorkerPayload
from workers.retry import build_retry_queue_payload


def _ad_row(
    *,
    sku: str,
    title: str,
    price: int,
    advertising: bool = True,
    currency: str = "IRR",
    updated_at: str | None = None,
) -> dict:
    row = {
        "sku": sku,
        "title": title,
        "cash_price": price,
        "currency": currency,
        "advertising": advertising,
    }
    if updated_at:
        row["updated_at"] = updated_at
    return row


def _eligible_rows(n: int, *, price_base: int = 10_000_000) -> list[dict]:
    return [
        _ad_row(
            sku=f"SKU-{i}",
            title=f"محصول ABC مدل {i}",
            price=price_base + i,
        )
        for i in range(1, n + 1)
    ]


@pytest.fixture(autouse=True)
def _reset_provider():
    set_product_feed_provider(None)
    yield
    set_product_feed_provider(None)


# ── provider / canonical ──────────────────────────────────────────


def test_missing_id_name_and_invalid_price_discarded():
    now = datetime.now(timezone.utc)
    kwargs = dict(default_currency="IRR", default_source="t", fetched_at=now)
    missing_id, reason_id = canonicalize_product_row(
        {"title": "نام", "cash_price": 10, "currency": "IRR", "advertising": True},
        **kwargs,
    )
    assert missing_id is None and reason_id == "missing_external_id"
    missing_name, reason_name = canonicalize_product_row(
        {"sku": "X", "cash_price": 10, "currency": "IRR", "advertising": True},
        **kwargs,
    )
    assert missing_name is None and reason_name == "missing_name"
    bad_price, reason_price = canonicalize_product_row(
        {"sku": "X", "title": "نام", "cash_price": -1, "currency": "IRR", "advertising": True},
        **kwargs,
    )
    assert bad_price is None and reason_price == "invalid_price"
    float_price, reason_float = canonicalize_product_row(
        {"sku": "X", "title": "نام", "cash_price": 12.5, "currency": "IRR", "advertising": True},
        **kwargs,
    )
    assert float_price is None and reason_float == "invalid_price"


def test_currency_explicit_no_rial_toman_guess():
    now = datetime.now(timezone.utc)
    row = {
        "sku": "C1",
        "title": "کالا",
        "cash_price": 28500000,
        "currency": "IRR",
        "advertising": True,
    }
    product, reason = canonicalize_product_row(
        row, default_currency="IRR", default_source="t", fetched_at=now
    )
    assert reason is None
    assert product is not None
    assert product.currency == "IRR"
    assert product.price == Decimal("28500000")
    inherited, _ = canonicalize_product_row(
        {k: v for k, v in row.items() if k != "currency"},
        default_currency="IRR",
        default_source="t",
        fetched_at=now,
    )
    assert inherited is not None
    assert inherited.currency == "IRR"
    assert inherited.price == Decimal("28500000")


def test_advertising_false_excluded_and_not_inferred():
    now = datetime.now(timezone.utc)
    cheap = {
        "sku": "X",
        "title": "ارزان",
        "cash_price": 1,
        "currency": "IRR",
        "category": "promo-looking",
    }
    assert is_explicitly_advertising(cheap) is False
    product, reason = canonicalize_product_row(
        cheap, default_currency="IRR", default_source="t", fetched_at=now
    )
    assert product is None
    assert reason == "not_advertising"

    tagged = dict(cheap, tags=["advertising"])
    product, reason = canonicalize_product_row(
        tagged, default_currency="IRR", default_source="t", fetched_at=now
    )
    assert product is not None
    assert product.advertising is True


def test_invalid_rows_discarded_until_minimum():
    rows = _eligible_rows(4) + [
        {"advertising": True, "title": "no id", "cash_price": 1, "currency": "IRR"},
        {"advertising": True, "sku": "N", "cash_price": 1, "currency": "IRR"},
        {"advertising": True, "sku": "P", "title": "bad", "cash_price": -3, "currency": "IRR"},
        {"advertising": False, "sku": "Z", "title": "no", "cash_price": 9, "currency": "IRR"},
    ]
    provider = FakeProductFeedProvider(rows)
    result = provider.fetch_advertising_products()
    assert len(result.products) == 4
    assert result.discarded_invalid >= 3


def test_insufficient_and_empty_feed():
    provider = FakeProductFeedProvider(_eligible_rows(2))
    set_product_feed_provider(provider)
    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products()
    assert exc.value.code == INSUFFICIENT_ADVERTISING_PRODUCTS

    set_product_feed_provider(FakeProductFeedProvider([]))
    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products()
    assert exc.value.code == PRODUCT_FEED_EMPTY


def test_stale_source_fail_closed(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    rows = [_ad_row(sku=f"S{i}", title=f"T{i}", price=100 + i, updated_at=old) for i in range(3)]
    provider = FakeProductFeedProvider(rows)
    set_product_feed_provider(provider)
    monkeypatch.setattr(
        "core_engine.services.product_feed.service._max_staleness_seconds",
        lambda: 60,
    )
    with pytest.raises(ProductFeedError) as exc:
        fetch_current_advertising_products()
    assert exc.value.code == PRODUCT_FEED_STALE


def test_http_empty_url_is_config_pending():
    provider = HttpJsonProductFeedProvider(base_url="")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == CONFIG_PENDING


def test_http_timeout_and_invalid_json(monkeypatch):
    class _TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "Client", _TimeoutClient)
    provider = HttpJsonProductFeedProvider(base_url="https://example.invalid/feed")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_TIMEOUT
    assert "/products" not in provider.base_url


def test_http_uses_configured_url_only(monkeypatch):
    seen: list[str] = []

    class _Resp:
        status_code = 200
        content = b'{"items":[]}'

        def json(self):
            return {"items": []}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            seen.append(url)
            assert "Authorization" not in str(headers) or "secret-token" in headers.get(
                "Authorization", ""
            )
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    provider = HttpJsonProductFeedProvider(
        base_url="https://assistant.example/live-board",
        token="secret-token",
    )
    result = provider.fetch_advertising_products()
    assert seen == ["https://assistant.example/live-board"]
    assert result.products == ()


def test_http_invalid_json_and_shape(monkeypatch):
    class _BadJson:
        status_code = 200
        content = b"not-json"

        def json(self):
            raise json.JSONDecodeError("expecting value", "not-json", 0)

    class _BadShape:
        status_code = 200
        content = b'{"foo":1}'

        def json(self):
            return {"foo": 1}

    class _Client:
        payload = _BadJson()

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            return self.payload

    monkeypatch.setattr(httpx, "Client", _Client)
    provider = HttpJsonProductFeedProvider(base_url="https://example.invalid/feed")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_INVALID_RESPONSE

    _Client.payload = _BadShape()
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_INVALID_RESPONSE


def test_connection_failure(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "Client", _Client)
    provider = HttpJsonProductFeedProvider(base_url="https://example.invalid/feed")
    with pytest.raises(ProductFeedError) as exc:
        provider.fetch_advertising_products()
    assert exc.value.code == PRODUCT_FEED_UNAVAILABLE


# ── selection ─────────────────────────────────────────────────────


def test_selection_counts_and_no_duplicates():
    feed = FakeProductFeedProvider(_eligible_rows(3)).fetch_advertising_products()
    selected = select_advertising_products(feed.products, random.Random(1))
    assert len(selected) == 3

    feed4 = FakeProductFeedProvider(_eligible_rows(4)).fetch_advertising_products()
    selected4 = select_advertising_products(feed4.products, random.Random(2))
    assert 3 <= len(selected4) <= 4

    feed_big = FakeProductFeedProvider(_eligible_rows(20)).fetch_advertising_products()
    selected_big = select_advertising_products(feed_big.products, random.Random(3))
    assert 3 <= len(selected_big) <= 5
    ids = [p.external_id for p in selected_big]
    assert len(ids) == len(set(ids))
    assert all(p.advertising for p in selected_big)


def test_recipient_variation_with_seeded_rng():
    feed = FakeProductFeedProvider(_eligible_rows(12)).fetch_advertising_products()
    a = select_advertising_products(feed.products, random.Random("c1:r1"))
    b = select_advertising_products(feed.products, random.Random("c1:r2"))
    assert {p.external_id for p in a} != {p.external_id for p in b}
    same_a = select_advertising_products(feed.products, random.Random("c1:r1"))
    assert [p.external_id for p in a] == [p.external_id for p in same_a]


# ── price integrity / composition ─────────────────────────────────


def test_exact_name_and_price_no_rial_toman_conversion():
    rows = [
        _ad_row(
            sku="ABC-123",
            title="محصول ABC مدل 123",
            price=28_500_000,
            currency="IRR",
        ),
        _ad_row(sku="Y", title="ماشین لباسشویی مدل Y", price=36_900_000),
        _ad_row(sku="Z", title="یخچال مدل Z", price=72_000_000),
    ]
    feed = FakeProductFeedProvider(rows).fetch_advertising_products()
    composition = compose_campaign_message(
        "سلام مشتری",
        feed.products,
        heading="محصولات ویژه امروز:",
        unit_label="ریال",
        rng=random.Random(0),
    )
    assert "محصول ABC مدل 123" in composition.final_text
    expected = format_price_display(Decimal("28500000"), "ریال")
    assert expected in composition.final_text
    assert composition.snapshot is not None
    abc = next(p for p in composition.snapshot.products if p.external_id == "ABC-123")
    assert abc.price == "28500000"
    assert abc.name == "محصول ABC مدل 123"
    # Accidental /10 toman conversion would produce ۲٬۸۵۰٬۰۰۰
    converted = format_price_display(Decimal("2850000"), "ریال")
    assert converted not in composition.final_text
    assert composition.final_text.endswith(composition.immutable_product_block)
    assert composition.immutable_product_block.startswith("محصولات ویژه امروز:")


def test_gpt_can_only_rewrite_prose():
    feed = FakeProductFeedProvider(_eligible_rows(3)).fetch_advertising_products()
    original = compose_campaign_message(
        "متن اولیه",
        feed.products,
        heading="محصولات ویژه امروز:",
        rng=random.Random(0),
        unit_label="ریال",
    )
    rewritten = compose_with_locked_products("متن بازنویسی‌شده GPT", original.snapshot)
    assert rewritten.immutable_product_block == original.immutable_product_block
    assert rewritten.snapshot == original.snapshot
    assert "متن بازنویسی‌شده GPT" in rewritten.final_text
    assert rewritten.immutable_product_block in rewritten.final_text


# ── campaign prepare / retry ──────────────────────────────────────


def _campaign(session, *, include_products: bool, n_contacts: int = 1, tag: str = "x"):
    suffix = uuid.uuid4().hex[:10]
    account = Account(
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label=f"p55-sender-{tag}",
        phone_number=f"989{suffix[:8]}",
    )
    session.add(account)
    campaign = Campaign(
        name="p55",
        title="p55",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        template_text="سلام {{first_name}}، پیام کمپین.",
        status=CampaignStatus.DRAFT.value,
        include_products=include_products,
        use_gpt=False,
    )
    session.add(campaign)
    session.flush()
    contacts = []
    for i in range(n_contacts):
        contact = Contact(
            first_name=f"مشتری{i}",
            phone=f"+9891{suffix[:7]}{i:02d}",
            phone_e164=f"+9891{suffix[:7]}{i:02d}",
            consent_status="allowed",
        )
        session.add(contact)
        session.flush()
        session.add(CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id))
        contacts.append(contact)
    session.commit()
    return campaign, contacts


def test_include_products_false_never_calls_provider(pg_session_factory):
    session = pg_session_factory()
    provider = FakeProductFeedProvider(_eligible_rows(5))
    set_product_feed_provider(provider)
    campaign, _ = _campaign(session, include_products=False, tag="off")
    result = prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    assert result.ready_count == 1
    assert provider.call_count == 0
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).one()
    assert "محصول ABC" not in rendered.final_text
    assert rendered.used_products is False
    session.close()


def test_prepare_injects_frozen_products_and_retry_keeps_text(pg_session_factory):
    session = pg_session_factory()
    rows = [
        _ad_row(sku="A", title="محصول ABC مدل 123", price=28_500_000),
        _ad_row(sku="B", title="ماشین لباسشویی مدل Y", price=36_900_000),
        _ad_row(sku="C", title="یخچال مدل Z", price=72_000_000),
        _ad_row(sku="D", title="جاروبرقی مدل D", price=9_000_000),
        _ad_row(sku="E", title="مایکروویو مدل E", price=8_000_000),
    ]
    provider = FakeProductFeedProvider(rows)
    set_product_feed_provider(provider)
    campaign, contacts = _campaign(session, include_products=True, n_contacts=2, tag="retry")
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    assert provider.call_count == 1  # one fetch per prepare, not per recipient

    rendered_rows = (
        session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign.id)
        .order_by(RenderedMessage.contact_id.asc())
        .all()
    )
    assert len(rendered_rows) == 2
    texts = {row.final_text for row in rendered_rows}
    assert len(texts) == 2  # recipient variation
    first = rendered_rows[0]
    assert first.used_products is True
    assert "سلام" in first.final_text
    frozen = (first.queue_payload or {}).get("metadata", {}).get("frozen_product_snapshot")
    assert frozen and frozen["products"]
    assert 3 <= len(frozen["products"]) <= 5
    original_text = first.final_text
    original_prices = [p["price"] for p in frozen["products"]]
    names = [p["name"] for p in frozen["products"]]
    for name in names:
        assert name in original_text
    assert all(price != "1" for price in original_prices)

    # Provider later returns different prices — retry must not refetch/reselect.
    provider.rows = [
        _ad_row(sku=row["sku"], title=row["title"], price=1) for row in rows
    ]
    calls_after_render = provider.call_count
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.rendered_message_id == first.id)
        .one()
    )
    raw = json.dumps(staged.queue_payload, ensure_ascii=False)
    payload = WorkerPayload.model_validate(normalize_queue_payload(dict(staged.queue_payload)))
    retry_raw = build_retry_queue_payload(raw, payload)
    retry_normalized = normalize_queue_payload(json.loads(retry_raw))
    transport = MagicMock()
    transport.send(retry_normalized["message_text"])
    transport.send.assert_called_once_with(original_text)
    session.refresh(first)
    assert first.final_text == original_text
    assert provider.call_count == calls_after_render
    freeze_after = (first.queue_payload or {}).get("metadata", {}).get("frozen_product_snapshot")
    assert freeze_after["products"] == frozen["products"]
    assert all(item["price"] != "1" for item in freeze_after["products"])
    session.close()


def test_feed_failure_blocks_prepare_and_queue(pg_session_factory):
    session = pg_session_factory()
    provider = FakeProductFeedProvider(fail_code=PRODUCT_FEED_UNAVAILABLE)
    set_product_feed_provider(provider)
    campaign, _ = _campaign(session, include_products=True, tag="fail")
    with pytest.raises(HTTPException) as exc:
        prepare_campaign_messages(
            session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == PRODUCT_FEED_UNAVAILABLE
    assert "اطلاعات محصولات و قیمت‌های لحظه‌ای در دسترس نیست" in exc.value.detail["message"]
    assert session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).count() == 0
    assert session.query(RenderedMessage).filter_by(campaign_id=campaign.id).count() == 0
    assert session.query(Message).filter_by(campaign_id=campaign.id).count() == 0
    session.close()


def test_queued_item_not_mutated_on_reprepare(pg_session_factory):
    session = pg_session_factory()
    provider = FakeProductFeedProvider(_eligible_rows(6, price_base=50_000))
    set_product_feed_provider(provider)
    campaign, _ = _campaign(session, include_products=True, tag="queued")
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    staged = session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).one()
    original = staged.final_text
    staged.status = StagedQueueItemStatus.QUEUED.value
    session.commit()
    provider.rows = _eligible_rows(6, price_base=1)
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    session.refresh(staged)
    assert staged.final_text == original
    session.close()
