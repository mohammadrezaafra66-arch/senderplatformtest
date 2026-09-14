"""Automatic campaign preparation on create/update — global workflow contract."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    ConsentStatus,
    Message,
    PlatformType,
    RenderStatus,
    RenderedMessage,
)
from core_engine.services.message_variation.fake_provider import FakeMessageVariationProvider
from core_engine.services.message_variation.service import set_message_variation_provider
from core_engine.services.product_feed.errors import INSUFFICIENT_ADVERTISING_PRODUCTS
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.service import set_product_feed_provider

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}
TEMPLATE = "سلام {{first_name}}، پیام تست."
TEMPLATE_GPT = "سلام {{first_name}}، برای اطلاع از قیمت‌های امروز با ما در ارتباط باشید."


def _gpt_variations(n: int = 3) -> list[str]:
    return [
        "سلام {{first_name}} عزیز، نسخه " + str(i) + " اطلاع‌رسانی امروز آماده است."
        for i in range(1, n + 1)
    ]


def _ad_rows(count: int = 5) -> list[dict]:
    return [
        {
            "sku": f"P-{idx}",
            "title": f"محصول {idx}",
            "cash_price": 10_000_000 + idx,
            "currency": "IRR",
            "advertising": True,
        }
        for idx in range(1, count + 1)
    ]


def _account(session) -> Account:
    account = Account(
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label=f"auto-prep-{uuid.uuid4().hex[:8]}",
    )
    session.add(account)
    session.flush()
    return account


def _contact(session) -> Contact:
    contact = Contact(
        first_name="علی",
        phone=f"+98912{uuid.uuid4().int % 10**7:07d}",
        phone_e164=f"+98912{uuid.uuid4().int % 10**7:07d}",
        consent_status=ConsentStatus.ALLOWED.value,
    )
    session.add(contact)
    session.flush()
    return contact


def _create_via_api(
    session,
    *,
    use_gpt: bool = False,
    include_products: bool = False,
    template_text: str = TEMPLATE,
    account_ids: list[int] | None = None,
) -> dict:
    contact = _contact(session)
    account = _account(session)
    session.commit()
    ids = account_ids if account_ids is not None else [account.id]
    title = f"auto-prep-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/campaigns/from-contacts",
        json={
            "contact_ids": [contact.id],
            "title": title,
            "platform": PlatformType.RUBIKA.value,
            "template_text": template_text,
            "use_gpt": use_gpt,
            "include_products": include_products,
            "account_ids": ids,
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_new_campaign_auto_prepares_plain_text(pg_session_factory):
    """Golden E2E: create workflow auto-prepares without manual prepare call."""
    session = pg_session_factory()
    body = _create_via_api(session, use_gpt=False, include_products=False)
    campaign_id = body["campaign_id"]
    auto = body.get("auto_prepare") or {}
    assert auto.get("prepared") is True or auto.get("ready_count", 0) >= 1

    session.expire_all()
    campaign = session.query(Campaign).filter(Campaign.id == campaign_id).one()
    assert campaign.status == CampaignStatus.PREPARED.value

    recipient = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign_id)
        .one()
    )
    assert recipient.render_status == RenderStatus.RENDERED
    assert (
        session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign_id)
        .count()
        == 1
    )

    preflight = client.get(f"/campaigns/{campaign_id}/preflight").json()
    assert preflight["campaign_prepared"] is True
    assert not any(
        item.get("code") == "CAMPAIGN_NOT_PREPARED"
        for item in preflight.get("blockers") or []
    )


def test_auto_prepare_after_sender_update(pg_session_factory):
    """Assigning senders on a valid draft triggers auto-prepare."""
    session = pg_session_factory()
    contact = _contact(session)
    account = _account(session)
    campaign = Campaign(
        name="draft-no-sender",
        title="draft-no-sender",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        template_text=TEMPLATE,
        use_gpt=False,
        include_products=False,
        status=CampaignStatus.DRAFT.value,
    )
    session.add(campaign)
    session.flush()
    session.add(
        CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id)
    )
    session.commit()

    response = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [account.id]},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    auto = response.json().get("auto_prepare") or {}
    assert auto.get("prepared") is True or auto.get("ready_count", 0) >= 1

    session.expire_all()
    campaign = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert campaign.status == CampaignStatus.PREPARED.value


def test_auto_prepare_idempotent_on_create(pg_session_factory):
    """Repeated prepare attempts must not duplicate final messages."""
    session = pg_session_factory()
    body = _create_via_api(session)
    campaign_id = body["campaign_id"]

    retry = client.post(f"/campaigns/{campaign_id}/prepare", json={}, headers=AUTH)
    assert retry.status_code == 200

    assert (
        session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign_id)
        .count()
        == 1
    )
    assert (
        session.query(Message).filter(Message.campaign_id == campaign_id).count() == 1
    )


def test_product_free_campaign_independent(pg_session_factory):
    """include_products=False must not surface product feed blockers."""
    session = pg_session_factory()
    set_product_feed_provider(FakeProductFeedProvider(_ad_rows()[:1]))
    try:
        body = _create_via_api(session, include_products=False)
        campaign_id = body["campaign_id"]
        preflight = client.get(f"/campaigns/{campaign_id}/preflight").json()
        prep_blockers = preflight.get("preparation_blockers") or []
        assert not any(
            item.get("code") == INSUFFICIENT_ADVERTISING_PRODUCTS for item in prep_blockers
        )
        assert preflight["campaign_prepared"] is True
    finally:
        set_product_feed_provider(None)


def test_products_on_auto_prepare_pass(pg_session_factory):
    session = pg_session_factory()
    set_product_feed_provider(FakeProductFeedProvider(_ad_rows()))
    try:
        body = _create_via_api(session, include_products=True, use_gpt=False)
        assert (body.get("auto_prepare") or {}).get("prepared") is True
        preflight = client.get(f"/campaigns/{body['campaign_id']}/preflight").json()
        assert preflight["campaign_prepared"] is True
    finally:
        set_product_feed_provider(None)


def test_products_on_blocked_when_insufficient(pg_session_factory):
    session = pg_session_factory()
    set_product_feed_provider(FakeProductFeedProvider(_ad_rows()[:1]))
    try:
        body = _create_via_api(session, include_products=True)
        auto = body.get("auto_prepare") or {}
        assert auto.get("prepared") is False
        blockers = auto.get("blockers") or []
        assert any(
            item.get("code") == INSUFFICIENT_ADVERTISING_PRODUCTS for item in blockers
        )
    finally:
        set_product_feed_provider(None)


def test_gpt_auto_prepare_pass(pg_session_factory):
    session = pg_session_factory()
    gpt = FakeMessageVariationProvider(_gpt_variations())
    set_message_variation_provider(gpt)
    try:
        body = _create_via_api(
            session,
            use_gpt=True,
            include_products=False,
            template_text=TEMPLATE_GPT,
        )
        auto = body.get("auto_prepare") or {}
        assert auto.get("prepared") is True
        preflight = client.get(f"/campaigns/{body['campaign_id']}/preflight").json()
        assert preflight["campaign_prepared"] is True
    finally:
        set_message_variation_provider(None)


def test_gpt_auto_prepare_surfaces_failure(pg_session_factory):
    from core_engine.services.message_variation.errors import GptVariationError

    session = pg_session_factory()
    with patch(
        "core_engine.services.phase4_prepare.generate_validated_pool",
        side_effect=GptVariationError("GPT_RENDER_FAILED"),
    ):
        body = _create_via_api(session, use_gpt=True, include_products=False, template_text=TEMPLATE_GPT)
    auto = body.get("auto_prepare") or {}
    assert auto.get("prepared") is False
    assert auto.get("error_code") == "GPT_RENDER_FAILED"


def test_blocked_draft_stays_draft(pg_session_factory):
    session = pg_session_factory()
    body = _create_via_api(session, template_text="   ")
    auto = body.get("auto_prepare") or {}
    assert auto.get("prepared") is False
    blockers = auto.get("blockers") or []
    assert any(item.get("code") == "TEMPLATE_MISSING" for item in blockers)


def test_concurrent_auto_prepare_safe(pg_session_factory):
    """Two concurrent prepare attempts must not duplicate output."""
    from core_engine.services.campaign_auto_prepare import try_auto_prepare_campaign

    session = pg_session_factory()
    body = _create_via_api(session)
    campaign_id = body["campaign_id"]
    factory = pg_session_factory

    def _run() -> bool:
        db = factory()
        try:
            result = try_auto_prepare_campaign(
                db, campaign_id, trigger="concurrent_test", force=False
            )
            return bool(result.prepared or result.skipped)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _run(), range(2)))
    assert all(results)

    session.expire_all()
    assert (
        session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign_id)
        .count()
        == 1
    )
