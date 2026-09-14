"""Production prepare endpoint and preparation readiness contract."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    MessageAttempt,
    PlatformType,
    RenderStatus,
    RenderedMessage,
    StagedQueueItem,
)
from core_engine.services.message_variation.errors import GptVariationError
from core_engine.services.product_feed.errors import INSUFFICIENT_ADVERTISING_PRODUCTS
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.service import set_product_feed_provider

client = TestClient(app)

TEMPLATE = "سلام {{first_name}}، پیام تست."


def _ad_rows() -> list[dict]:
    return [
        {
            "sku": f"P-{idx}",
            "title": f"محصول {idx}",
            "cash_price": 10_000_000 + idx,
            "currency": "IRR",
            "advertising": True,
        }
        for idx in range(1, 6)
    ]


@pytest.fixture
def prepare_env(pg_session_factory):
    session = pg_session_factory()
    created_campaigns: list[int] = []
    created_contacts: list[int] = []
    created_accounts: list[int] = []

    sender = Account(
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label="prepare-test-sender",
    )
    session.add(sender)
    session.flush()
    created_accounts.append(sender.id)

    def _factory(
        *,
        title: str = "prepare-test",
        template_text: str = TEMPLATE,
        include_products: bool = False,
        use_gpt: bool = False,
        with_recipient: bool = True,
        with_sender: bool = True,
        phone: str = "+989120000200",
    ) -> Campaign:
        campaign = Campaign(
            name=title,
            title=title,
            channel="rubika",
            platform=PlatformType.RUBIKA,
            template_text=template_text,
            include_products=include_products,
            use_gpt=use_gpt,
            status=CampaignStatus.DRAFT.value,
        )
        session.add(campaign)
        session.flush()
        if with_recipient:
            contact = Contact(
                first_name="علی",
                phone=phone,
                phone_e164=phone,
                consent_status="allowed",
            )
            session.add(contact)
            session.flush()
            created_contacts.append(contact.id)
            session.add(
                CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id)
            )
        if with_sender:
            session.add(
                CampaignAccount(
                    campaign_id=campaign.id,
                    account_id=sender.id,
                    priority=1,
                )
            )
        session.commit()
        created_campaigns.append(campaign.id)
        return campaign

    yield session, _factory

    for campaign_id in created_campaigns:
        message_ids = [
            row_id
            for (row_id,) in session.query(Message.id)
            .filter(Message.campaign_id == campaign_id)
            .all()
        ]
        if message_ids:
            session.query(MessageAttempt).filter(
                MessageAttempt.message_id.in_(message_ids)
            ).delete(synchronize_session=False)
        session.query(StagedQueueItem).filter(
            StagedQueueItem.campaign_id == campaign_id
        ).delete(synchronize_session=False)
        session.query(RenderedMessage).filter(
            RenderedMessage.campaign_id == campaign_id
        ).delete(synchronize_session=False)
        session.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id == campaign_id
        ).delete(synchronize_session=False)
        session.query(Message).filter(Message.campaign_id == campaign_id).delete(
            synchronize_session=False
        )
        session.query(CampaignAccount).filter(
            CampaignAccount.campaign_id == campaign_id
        ).delete(synchronize_session=False)
        session.query(Campaign).filter(Campaign.id == campaign_id).delete(
            synchronize_session=False
        )
    for contact_id in created_contacts:
        session.query(Contact).filter(Contact.id == contact_id).delete(
            synchronize_session=False
        )
    for account_id in created_accounts:
        session.query(Account).filter(Account.id == account_id).delete(
            synchronize_session=False
        )
    session.commit()
    session.close()


def test_prepare_pass_valid_campaign(prepare_env):
    """CASE 1: valid campaign with recipient, message, sender."""
    _session, factory = prepare_env
    campaign = factory()

    response = client.post(f"/campaigns/{campaign.id}/prepare", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ready_count"] == 1
    assert body["staged_count"] == 1


def test_prepare_blocks_missing_audience(prepare_env):
    """CASE 2: missing audience."""
    _session, factory = prepare_env
    campaign = factory(with_recipient=False)

    preflight = client.get(f"/campaigns/{campaign.id}/preflight")
    assert preflight.status_code == 200
    blockers = preflight.json().get("preparation_blockers") or []
    assert any(item["code"] == "NO_RECIPIENTS" for item in blockers)

    response = client.post(f"/campaigns/{campaign.id}/prepare", json={})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "NO_RECIPIENTS"


def test_prepare_blocks_missing_message(prepare_env):
    """CASE 3: missing message template."""
    _session, factory = prepare_env
    campaign = factory(template_text="   ")

    preflight = client.get(f"/campaigns/{campaign.id}/preflight")
    assert preflight.status_code == 200
    blockers = preflight.json().get("preparation_blockers") or []
    assert any(item["code"] == "TEMPLATE_MISSING" for item in blockers)

    response = client.post(f"/campaigns/{campaign.id}/prepare", json={})
    assert response.status_code == 400


def test_prepare_surfaces_gpt_failure(prepare_env):
    """CASE 4: GPT failure returns clear preparation failure."""
    _session, factory = prepare_env
    campaign = factory(use_gpt=True)

    with patch(
        "core_engine.services.phase4_prepare.generate_validated_pool",
        side_effect=GptVariationError("GPT_UNAVAILABLE"),
    ):
        response = client.post(f"/campaigns/{campaign.id}/prepare", json={})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "GPT_UNAVAILABLE"


def test_prepare_surfaces_product_feed_failure(prepare_env):
    """CASE 5: product data failure surfaces as preparation blocker."""
    _session, factory = prepare_env
    campaign = factory(include_products=True)
    set_product_feed_provider(FakeProductFeedProvider(_ad_rows()[:2]))
    try:
        preflight = client.get(f"/campaigns/{campaign.id}/preflight")
        assert preflight.status_code == 200
        blockers = preflight.json().get("preparation_blockers") or []
        assert any(
            item["code"] == INSUFFICIENT_ADVERTISING_PRODUCTS for item in blockers
        )

        response = client.post(f"/campaigns/{campaign.id}/prepare", json={})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == INSUFFICIENT_ADVERTISING_PRODUCTS
    finally:
        set_product_feed_provider(None)


def test_prepare_creates_one_final_message(prepare_env):
    """CASE 6: successful prepare creates exactly one final message."""
    _session, factory = prepare_env
    campaign = factory()

    response = client.post(f"/campaigns/{campaign.id}/prepare", json={})
    assert response.status_code == 200

    session = _session
    rendered = (
        session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign.id)
        .all()
    )
    messages = session.query(Message).filter(Message.campaign_id == campaign.id).all()
    recipient = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .one()
    )
    assert len(rendered) == 1
    assert len(messages) == 1
    assert recipient.render_status == RenderStatus.RENDERED


def test_plain_text_golden_prepare_without_products_or_gpt(prepare_env):
    """Plain text path: no products, no GPT → rendered recipient + final message."""
    _session, factory = prepare_env
    campaign = factory(include_products=False, use_gpt=False)

    response = client.post(f"/campaigns/{campaign.id}/prepare", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ready_count"] == 1

    session = _session
    recipient = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .one()
    )
    assert recipient.render_status == RenderStatus.RENDERED
    assert recipient.final_message_id is not None
    assert (
        session.query(Message).filter(Message.campaign_id == campaign.id).count() == 1
    )


def test_prepare_is_idempotent(prepare_env):
    """CASE 7: calling prepare twice does not duplicate final messages."""
    _session, factory = prepare_env
    campaign = factory()

    first = client.post(f"/campaigns/{campaign.id}/prepare", json={})
    second = client.post(f"/campaigns/{campaign.id}/prepare", json={})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["already_staged_count"] == 1
    assert second.json()["staged_count"] == 1

    session = _session
    rendered_count = (
        session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign.id)
        .count()
    )
    assert rendered_count == 1


def test_prepare_does_not_send(prepare_env):
    """CASE 8: prepare does not create external send attempts."""
    _session, factory = prepare_env
    campaign = factory()

    response = client.post(f"/campaigns/{campaign.id}/prepare", json={})
    assert response.status_code == 200

    session = _session
    message_ids = [
        row_id
        for (row_id,) in session.query(Message.id)
        .filter(Message.campaign_id == campaign.id)
        .all()
    ]
    attempts = (
        session.query(MessageAttempt)
        .filter(MessageAttempt.message_id.in_(message_ids))
        .count()
        if message_ids
        else 0
    )
    recipient = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .one()
    )
    assert attempts == 0
    assert recipient.send_status.value == "pending"


def test_golden_prepare_e2e_with_products(prepare_env):
    """Golden path: recipient + message + sender + products → one staged message."""
    _session, factory = prepare_env
    set_product_feed_provider(FakeProductFeedProvider(_ad_rows()))
    try:
        campaign = factory(include_products=True, use_gpt=False)
        response = client.post(f"/campaigns/{campaign.id}/prepare", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["ready_count"] == 1
        assert body["real_gpt_called"] is False

        preflight = client.get(f"/campaigns/{campaign.id}/preflight").json()
        assert preflight["campaign_prepared"] is True
    finally:
        set_product_feed_provider(None)
