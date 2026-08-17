"""Phase 5.7 sample preview + message log excerpt/detail APIs."""

from __future__ import annotations

import uuid

from core_engine.api.auth import get_current_user
from core_engine.main import app
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
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_render import LIST_EXCERPT_MAX_CHARS, hash_final_text
from core_engine.services.message_variation.fake_provider import FakeMessageVariationProvider
from core_engine.services.message_variation.service import (
    reset_preview_rate_guard,
    set_message_variation_provider,
)
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.service import set_product_feed_provider

AUTH = {"Authorization": "Bearer fake_token"}
TEMPLATE = "سلام {{first_name}}"


def _ad_rows():
    return [
        {
            "sku": f"S{i}",
            "title": f"کالا {i}",
            "cash_price": 1000 + i,
            "currency": "IRR",
            "advertising": True,
        }
        for i in range(5)
    ]


def test_render_preview_rejects_secrets(client, admin_auth):
    reset_preview_rate_guard()
    response = client.post(
        "/campaigns/render-preview",
        headers=AUTH,
        json={"template_text": TEMPLATE, "openai_api_key": "sk-secret"},
    )
    assert response.status_code == 422


def test_render_preview_four_modes_sample_label(client, admin_auth):
    reset_preview_rate_guard()
    set_message_variation_provider(
        FakeMessageVariationProvider(
            [
                "{{first_name}} عزیز سلام",
                "سلام {{first_name}} نسخه دو متن کافی.",
                "سلام {{first_name}} نسخه سه متن کافی.",
            ]
        )
    )
    set_product_feed_provider(FakeProductFeedProvider(_ad_rows()))
    try:
        for use_gpt, include_products in (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        ):
            reset_preview_rate_guard()
            response = client.post(
                "/campaigns/render-preview",
                headers=AUTH,
                json={
                    "template_text": TEMPLATE,
                    "use_gpt": use_gpt,
                    "include_products": include_products,
                    "preview_count": 3,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["committed"] is False
            assert body["preview_kind"] == "sample"
            assert body["label"] == "پیش‌نمایش نمونه"
            assert len(body["samples"]) == 3
            sample = body["samples"][0]
            assert "نمونه" in sample["final_text"] or sample["final_text"]
            assert sample["committed"] is False
            if include_products:
                assert sample["include_products"] is True
                assert sample["final_text"].endswith(sample["immutable_product_block"])
            if use_gpt:
                assert sample["use_gpt"] is True
                assert sample["variation_id"]
        assert "key" not in str(body).lower()
        assert "token" not in str(body).lower()
    finally:
        set_message_variation_provider(None)
        set_product_feed_provider(None)


def test_viewer_cannot_render_preview(client):
    async def _viewer():
        return {"username": "viewer", "password": "viewer123", "role": "viewer"}

    app.dependency_overrides[get_current_user] = _viewer
    try:
        response = client.post(
            "/campaigns/render-preview",
            headers=AUTH,
            json={"template_text": TEMPLATE, "use_gpt": False, "include_products": False},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _prepared_long_campaign(pg_session_factory):
    session = pg_session_factory()
    suffix = uuid.uuid4().hex[:10]
    account = Account(
        platform=PlatformType.BALE,
        status=AccountStatus.ACTIVE,
        label="p57-log",
        phone_number=f"989{suffix[:8]}",
    )
    session.add(account)
    long_text = "خط یک\nخط دو\n" + ("متن بلند " * 40)
    campaign = Campaign(
        name="p57-log",
        title="p57-log",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.DRAFT.value,
        template_text=long_text + " {{first_name}}",
        use_gpt=False,
        include_products=False,
    )
    session.add(campaign)
    session.flush()
    contact = Contact(
        first_name="مریم",
        phone=f"+9891{suffix[:8]}",
        phone_e164=f"+9891{suffix[:8]}",
        consent_status="allowed",
    )
    session.add(contact)
    session.flush()
    session.add(CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id))
    session.commit()
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest(force_mock_output=False))
    recipient = (
        session.query(CampaignRecipient).filter_by(campaign_id=campaign.id).one()
    )
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).one()
    return session, campaign, recipient, rendered


def test_message_list_excerpt_and_detail_full_text(client, admin_auth, pg_session_factory):
    session, campaign, recipient, rendered = _prepared_long_campaign(pg_session_factory)
    try:
        listed = client.get(
            f"/campaigns/{campaign.id}/recipients",
            headers=AUTH,
        )
        assert listed.status_code == 200
        item = listed.json()["items"][0]
        assert item["has_more"] is True
        assert item["has_long_text"] is True
        assert item["final_text_preview"] == rendered.final_text[:LIST_EXCERPT_MAX_CHARS]
        assert item["final_text_preview"] != rendered.final_text
        assert "final_text" not in item or item.get("final_text") is None
        assert item["final_text_sha256"] == hash_final_text(rendered.final_text)
        assert item["render_version"]
        assert item["render_batch_id"]

        detail = client.get(
            f"/campaigns/{campaign.id}/recipients/{recipient.id}",
            headers=AUTH,
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["final_text"] == rendered.final_text
        assert "\n" in body["final_text"]
        assert body["campaign_id"] == campaign.id
        assert body["contact_id"] == recipient.contact_id
        assert body["rendered_message_id"] == rendered.id
        assert body["message_id"]
        assert body["final_text_sha256"] == hash_final_text(rendered.final_text)
        assert "<script>" not in str(body.get("gpt"))
        campaign_detail = client.get(f"/campaigns/{campaign.id}", headers=AUTH)
        assert campaign_detail.status_code == 200
        samples = campaign_detail.json()["committed_renders"]
        assert samples
        assert samples[0]["final_text"] == rendered.final_text
        assert samples[0]["label"] == "پیام نهایی ثبت‌شده"
    finally:
        session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).delete()
        session.query(CampaignRecipient).filter_by(campaign_id=campaign.id).delete()
        session.query(Message).filter_by(campaign_id=campaign.id).delete()
        session.query(RenderedMessage).filter_by(campaign_id=campaign.id).delete()
        session.query(Campaign).filter_by(id=campaign.id).delete()
        session.commit()
        session.close()


def test_viewer_can_read_message_detail(client, pg_session_factory):
    session, campaign, recipient, _rendered = _prepared_long_campaign(pg_session_factory)

    async def _viewer():
        return {"username": "viewer", "password": "viewer123", "role": "viewer"}

    app.dependency_overrides[get_current_user] = _viewer
    try:
        response = client.get(
            f"/campaigns/{campaign.id}/recipients/{recipient.id}",
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.json()["final_text"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).delete()
        session.query(CampaignRecipient).filter_by(campaign_id=campaign.id).delete()
        session.query(Message).filter_by(campaign_id=campaign.id).delete()
        session.query(RenderedMessage).filter_by(campaign_id=campaign.id).delete()
        session.query(Campaign).filter_by(id=campaign.id).delete()
        session.commit()
        session.close()
