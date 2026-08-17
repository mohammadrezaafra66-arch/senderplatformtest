"""Phase 5.7 — exact render freeze, hash, preview, and message-log trace.

No live OpenAI, AfraKala, or Rubika send.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

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
from core_engine.services.campaign_render import (
    RENDER_VERSION,
    compose_final_render,
    excerpt_final_text,
    hash_final_text,
    new_render_batch_id,
)
from core_engine.services.message_variation.fake_provider import FakeMessageVariationProvider
from core_engine.services.message_variation.service import set_message_variation_provider
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.product_feed.errors import PRODUCT_FEED_UNAVAILABLE, ProductFeedError
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.service import set_product_feed_provider
from workers.payload_adapter import normalize_queue_payload
from workers.payloads import WorkerPayload
from workers.retry import build_retry_queue_payload

TEMPLATE = "سلام {{first_name}}"
GPT_VARIATION = "{{first_name}} عزیز سلام"


def _ad_rows() -> list[dict]:
    return [
        {
            "sku": "TV-X",
            "title": "تلویزیون سامسونگ مدل X",
            "cash_price": 28_500_000,
            "currency": "IRR",
            "advertising": True,
        },
        {
            "sku": "W-Y",
            "title": "ماشین لباسشویی مدل Y",
            "cash_price": 36_900_000,
            "currency": "IRR",
            "advertising": True,
        },
        {
            "sku": "F-Z",
            "title": "یخچال مدل Z",
            "cash_price": 72_000_000,
            "currency": "IRR",
            "advertising": True,
        },
        {
            "sku": "M-E",
            "title": "مایکروویو مدل E",
            "cash_price": 8_000_000,
            "currency": "IRR",
            "advertising": True,
        },
        {
            "sku": "A-1",
            "title": "جاروبرقی مدل A",
            "cash_price": 9_500_000,
            "currency": "IRR",
            "advertising": True,
        },
    ]


@pytest.fixture(autouse=True)
def _reset_providers():
    set_message_variation_provider(None)
    set_product_feed_provider(None)
    yield
    set_message_variation_provider(None)
    set_product_feed_provider(None)


def _campaign(
    session,
    *,
    use_gpt: bool,
    include_products: bool,
    n_contacts: int = 1,
    tag: str = "x",
    template: str = TEMPLATE,
    first_name: str = "مریم",
):
    suffix = uuid.uuid4().hex[:10]
    account = Account(
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label=f"p57-{tag}",
        phone_number=f"989{suffix[:8]}",
    )
    session.add(account)
    campaign = Campaign(
        name="p57",
        title="p57",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        template_text=template,
        status=CampaignStatus.DRAFT.value,
        include_products=include_products,
        use_gpt=use_gpt,
    )
    session.add(campaign)
    session.flush()
    contacts = []
    for i in range(n_contacts):
        contact = Contact(
            first_name=first_name if i == 0 else f"{first_name}{i}",
            phone=f"+9891{suffix[:7]}{i:02d}",
            phone_e164=f"+9891{suffix[:7]}{i:02d}",
            consent_status="allowed",
        )
        session.add(contact)
        session.flush()
        session.add(CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id))
        contacts.append(contact)
    session.commit()
    return campaign, contacts, account


def _transport_text(staged: StagedQueueItem) -> str:
    payload = WorkerPayload.model_validate(normalize_queue_payload(dict(staged.queue_payload)))
    return payload.message_text


def test_excerpt_is_unicode_safe_and_does_not_mutate():
    original = "الف" * 50 + "🎉" + "ب" * 50
    preview, has_more = excerpt_final_text(original, max_chars=20)
    assert has_more is True
    assert preview == original[:20]
    assert original.endswith("ب" * 50)


def test_hash_is_utf8_sha256():
    text = "مریم عزیز سلام"
    assert hash_final_text(text) == hash_final_text(text)
    assert hash_final_text(text) != hash_final_text(text + " ")


def test_compose_placeholder_gpt_path():
    result = compose_final_render(
        prose=GPT_VARIATION,
        render_batch_id="batch",
        template_source="gpt_variation",
        use_gpt=True,
        include_products=False,
        extra_variables={"first_name": "مریم"},
        substitute=True,
        sample=True,
    )
    assert result.final_text == "مریم عزیز سلام"
    assert result.final_text_sha256 == hash_final_text("مریم عزیز سلام")
    assert result.render_version == RENDER_VERSION


def test_mode_a_template_only(pg_session_factory):
    session = pg_session_factory()
    set_message_variation_provider(FakeMessageVariationProvider([GPT_VARIATION] * 3))
    set_product_feed_provider(FakeProductFeedProvider(_ad_rows()))
    campaign, contacts, _ = _campaign(
        session, use_gpt=False, include_products=False, tag="a"
    )
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest(force_mock_output=False))
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).one()
    message = session.query(Message).filter_by(campaign_id=campaign.id).one()
    staged = session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).one()
    expected = f"سلام {contacts[0].first_name}"
    meta = (rendered.queue_payload or {}).get("metadata") or {}
    assert rendered.final_text == expected
    assert message.rendered_text == expected
    assert staged.final_text == expected
    assert staged.queue_payload["final_text"] == expected
    assert _transport_text(staged) == expected
    assert meta["render_version"] == RENDER_VERSION
    assert meta["render_batch_id"]
    assert meta["final_text_sha256"] == hash_final_text(expected)
    session.close()


def test_mode_b_gpt_placeholder(pg_session_factory):
    session = pg_session_factory()
    gpt = FakeMessageVariationProvider(
        [
            GPT_VARIATION,
            "{{first_name}} عزیز درود، اطلاع‌رسانی امروز آماده است.",
            "{{first_name}} عزیز وقت بخیر، پیام امروز آماده است.",
        ]
    )
    set_message_variation_provider(gpt)
    campaign, _, _ = _campaign(session, use_gpt=True, include_products=False, tag="b")
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest(force_mock_output=False))
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).one()
    message = session.query(Message).filter_by(campaign_id=campaign.id).one()
    staged = session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).one()
    assert rendered.final_text.startswith("مریم عزیز")
    assert "{{" not in rendered.final_text
    assert message.rendered_text == rendered.final_text == _transport_text(staged)
    meta = (rendered.queue_payload or {}).get("metadata") or {}
    assert meta["gpt_variation"]["variation_id"]
    assert meta["gpt_variation"]["generation_batch_id"]
    assert gpt.call_count == 1
    session.close()


def test_mode_c_products_appended(pg_session_factory):
    session = pg_session_factory()
    products = FakeProductFeedProvider(_ad_rows())
    set_product_feed_provider(products)
    campaign, _, _ = _campaign(session, use_gpt=False, include_products=True, tag="c")
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest(force_mock_output=False))
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).one()
    staged = session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).one()
    meta = (rendered.queue_payload or {}).get("metadata") or {}
    block = meta["immutable_product_block"]
    assert rendered.final_text.startswith("سلام مریم")
    assert rendered.final_text.endswith(block)
    assert "تلویزیون سامسونگ مدل X" in block or "ماشین لباسشویی مدل Y" in block
    assert _transport_text(staged) == rendered.final_text
    session.close()


def test_mode_d_gpt_then_locked_products(pg_session_factory):
    session = pg_session_factory()
    gpt = FakeMessageVariationProvider(
        [GPT_VARIATION, "سلام {{first_name}} نسخه دو متن کافی امروز.", "سلام {{first_name}} نسخه سه متن کافی امروز."]
    )
    products = FakeProductFeedProvider(_ad_rows())
    set_message_variation_provider(gpt)
    set_product_feed_provider(products)
    campaign, contacts, _ = _campaign(session, use_gpt=True, include_products=True, tag="d")
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest(force_mock_output=False))
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).one()
    meta = (rendered.queue_payload or {}).get("metadata") or {}
    prose = meta["prose_text"]
    assert contacts[0].first_name in prose or "مریم" in prose
    assert rendered.final_text.startswith(prose)
    assert rendered.final_text.endswith(meta["immutable_product_block"])
    snapshot = meta["frozen_product_snapshot"]
    names = [item["name"] for item in snapshot["products"]]
    assert names
    assert all(name in rendered.final_text for name in names)
    session.close()


def test_retry_and_restart_same_text(pg_session_factory):
    session = pg_session_factory()
    gpt = FakeMessageVariationProvider(
        [GPT_VARIATION, "سلام {{first_name}} نسخه دو متن کافی امروز.", "سلام {{first_name}} نسخه سه متن کافی امروز."]
    )
    products = FakeProductFeedProvider(_ad_rows())
    set_message_variation_provider(gpt)
    set_product_feed_provider(products)
    campaign, _, _ = _campaign(
        session, use_gpt=True, include_products=True, n_contacts=1, tag="retry"
    )
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest(force_mock_output=False))
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).one()
    staged = session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).one()
    original = rendered.final_text
    original_hash = (rendered.queue_payload or {})["metadata"]["final_text_sha256"]
    gpt_calls = gpt.call_count
    product_calls = products.call_count
    gpt.fixed_variations = ["باید عوض شود {{first_name}} متن کافی برای تنوع جدید."] * 3
    products.rows = [{**row, "cash_price": 1} for row in _ad_rows()]

    transport = MagicMock()
    transport.send(_transport_text(staged))
    retry_raw = build_retry_queue_payload(
        json.dumps(staged.queue_payload, ensure_ascii=False),
        WorkerPayload.model_validate(normalize_queue_payload(dict(staged.queue_payload))),
    )
    retry_payload = normalize_queue_payload(json.loads(retry_raw))
    transport.send(retry_payload["message_text"])
    assert transport.send.call_args_list[0].args[0] == original
    assert transport.send.call_args_list[1].args[0] == original
    assert gpt.call_count == gpt_calls
    assert products.call_count == product_calls

    campaign_id = campaign.id
    session.close()
    session2 = pg_session_factory()
    staged2 = session2.query(StagedQueueItem).filter_by(campaign_id=campaign_id).one()
    rendered2 = session2.query(RenderedMessage).filter_by(campaign_id=campaign_id).one()
    assert _transport_text(staged2) == original == rendered2.final_text
    assert (rendered2.queue_payload or {})["metadata"]["final_text_sha256"] == original_hash
    session2.close()


def test_product_and_gpt_trace_frozen_against_live_changes(pg_session_factory):
    session = pg_session_factory()
    gpt = FakeMessageVariationProvider(
        [GPT_VARIATION, "سلام {{first_name}} نسخه دو متن کافی امروز.", "سلام {{first_name}} نسخه سه متن کافی امروز."]
    )
    products = FakeProductFeedProvider(_ad_rows())
    set_message_variation_provider(gpt)
    set_product_feed_provider(products)
    campaign, _, _ = _campaign(session, use_gpt=True, include_products=True, tag="trace")
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest(force_mock_output=False))
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).one()
    meta = (rendered.queue_payload or {}).get("metadata") or {}
    variation_id = meta["gpt_variation"]["variation_id"]
    batch_id = meta["gpt_variation"]["generation_batch_id"]
    frozen = meta["frozen_product_snapshot"]["products"]
    products.rows = [{**row, "title": "نام جدید", "cash_price": 1} for row in _ad_rows()]
    gpt.fixed_variations = ["تغییر کامل {{first_name}} متن کافی برای تنوع."] * 3
    session.refresh(rendered)
    later = (rendered.queue_payload or {})["metadata"]
    assert later["gpt_variation"]["variation_id"] == variation_id
    assert later["gpt_variation"]["generation_batch_id"] == batch_id
    assert later["frozen_product_snapshot"]["products"] == frozen
    session.close()


def test_prepare_failure_rolls_back_partial_batch(pg_session_factory, monkeypatch):
    session = pg_session_factory()
    set_product_feed_provider(FakeProductFeedProvider(_ad_rows()))
    campaign, _, _ = _campaign(
        session, use_gpt=False, include_products=True, n_contacts=2, tag="tx"
    )
    calls = {"n": 0}
    import core_engine.services.campaign_render as render_mod

    original = render_mod.select_and_compose

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise ProductFeedError(PRODUCT_FEED_UNAVAILABLE)
        return original(*args, **kwargs)

    monkeypatch.setattr(render_mod, "select_and_compose", boom)
    with pytest.raises(HTTPException):
        prepare_campaign_messages(
            session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
        )
    other = pg_session_factory()
    assert other.query(RenderedMessage).filter_by(campaign_id=campaign.id).count() == 0
    assert other.query(StagedQueueItem).filter_by(campaign_id=campaign.id).count() == 0
    assert other.query(Message).filter_by(campaign_id=campaign.id).count() == 0
    other.close()
    session.close()


def test_same_batch_id_on_one_prepare(pg_session_factory):
    session = pg_session_factory()
    campaign, _, _ = _campaign(
        session, use_gpt=False, include_products=False, n_contacts=3, tag="batch"
    )
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest(force_mock_output=False))
    rows = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).all()
    batches = {
        (row.queue_payload or {}).get("metadata", {}).get("render_batch_id") for row in rows
    }
    assert len(batches) == 1
    assert None not in batches
    assert new_render_batch_id() not in batches
    session.close()
