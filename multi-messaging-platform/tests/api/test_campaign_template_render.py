"""The campaign's own text must reach the staged message, not a placeholder.

Regression cover for the bug where every campaign produced the same fixed
dry-run sentence regardless of what the operator typed.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

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
from core_engine.services import campaign_control
from core_engine.services.phase4_prepare import (
    build_phase4_mock_final_text,
    prepare_campaign_messages,
    render_campaign_template,
)

client = TestClient(app)

DRY_RUN_MARKER = "این پیام فقط تست dry-run است"
# Verbatim from the field report, minus the contact name.
REPORTED_SENTENCE_FRAGMENT = (
    "عزیز، چند محصول موجود با قیمت روز آماده بررسی است. "
    "این پیام فقط تست dry-run است و ارسال واقعی انجام نشده."
)

TEMPLATE_A = "سلام {{first_name}}، قیمت جدید تلویزیون آماده است."
TEMPLATE_B = "{{first_name}} عزیز، موجودی یخچال به‌روزرسانی شد."


def _make_campaign(session, *, title, template_text):
    campaign = Campaign(
        name=title,
        title=title,
        channel="rubika",
        platform=PlatformType.RUBIKA,
        template_text=template_text,
        status=CampaignStatus.DRAFT.value,
    )
    session.add(campaign)
    session.flush()
    return campaign


def _attach_contact(session, campaign, *, first_name, phone):
    """Contacts hang off campaign_recipients; campaign_id stays NULL."""
    contact = Contact(
        first_name=first_name,
        phone=phone,
        phone_e164=phone,
        consent_status="allowed",
    )
    session.add(contact)
    session.flush()
    session.add(
        CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id)
    )
    session.flush()
    return contact


@pytest.fixture
def campaign_env(pg_session_factory):
    session = pg_session_factory()
    created_campaigns: list[int] = []
    created_contacts: list[int] = []
    sender = Account(
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label="template-test-sender",
    )
    session.add(sender)
    session.commit()

    def _factory(*, title, template_text, first_name, phone):
        campaign = _make_campaign(session, title=title, template_text=template_text)
        contact = _attach_contact(
            session, campaign, first_name=first_name, phone=phone
        )
        session.commit()
        created_campaigns.append(campaign.id)
        created_contacts.append(contact.id)
        return campaign, contact

    yield session, _factory

    for campaign_id in created_campaigns:
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
        session.query(Campaign).filter(Campaign.id == campaign_id).delete(
            synchronize_session=False
        )
    for contact_id in created_contacts:
        session.query(Contact).filter(Contact.id == contact_id).delete(
            synchronize_session=False
        )
    session.query(Account).filter(Account.id == sender.id).delete(
        synchronize_session=False
    )
    session.commit()
    session.close()


def test_campaign_stores_the_operator_text_verbatim(campaign_env):
    """Test 1 — the text is persisted exactly as written."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="verbatim",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000101",
    )
    session.expire_all()
    stored = session.query(Campaign).filter(Campaign.id == campaign.id).first()
    assert stored.template_text == TEMPLATE_A


def test_prepare_uses_campaign_text_and_substitutes_name(campaign_env):
    """Tests 2, 3 and 5 — real text, name substituted, no dry-run marker."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="render",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000102",
    )

    result = prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )

    assert result.ready_count == 1
    expected = "سلام مهرداد، قیمت جدید تلویزیون آماده است."

    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    rendered = (
        session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign.id)
        .one()
    )

    # Test 5 — RenderedMessage.final_text
    assert rendered.final_text == expected
    # Test 6 — StagedQueueItem.final_text
    assert staged.final_text == expected
    # Test 7 — queue payload
    assert staged.queue_payload["final_text"] == expected

    assert DRY_RUN_MARKER not in expected
    assert "{{" not in expected


def test_two_campaigns_produce_different_text(campaign_env):
    """Test 4 — different templates must not collapse to one output."""
    session, factory = campaign_env
    campaign_a, _ = factory(
        title="camp-a",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000103",
    )
    campaign_b, _ = factory(
        title="camp-b",
        template_text=TEMPLATE_B,
        first_name="مطهره",
        phone="+989120000104",
    )

    prepare_campaign_messages(
        session, campaign_a.id, PrepareMessagesRequest(force_mock_output=False)
    )
    prepare_campaign_messages(
        session, campaign_b.id, PrepareMessagesRequest(force_mock_output=False)
    )

    text_a = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_a.id)
        .one()
        .final_text
    )
    text_b = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_b.id)
        .one()
        .final_text
    )

    assert text_a != text_b
    assert text_a == "سلام مهرداد، قیمت جدید تلویزیون آماده است."
    assert text_b == "مطهره عزیز، موجودی یخچال به‌روزرسانی شد."
    assert DRY_RUN_MARKER not in text_a
    assert DRY_RUN_MARKER not in text_b


def test_mock_mode_still_available_on_request(campaign_env):
    """force_mock_output=True keeps the placeholder path for dry runs."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="mock",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000105",
    )

    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=True)
    )

    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert DRY_RUN_MARKER in staged.final_text


def test_campaign_without_text_fails_loudly(campaign_env):
    """Test 6 of the brief — an empty template must not silently send anything."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="empty",
        template_text="   ",
        first_name="مهرداد",
        phone="+989120000106",
    )

    with pytest.raises(HTTPException) as excinfo:
        prepare_campaign_messages(
            session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
        )

    assert excinfo.value.status_code == 400
    assert "template_text" in str(excinfo.value.detail)

    assert (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .count()
        == 0
    )


def test_ready_item_is_refreshed_when_template_changes(campaign_env):
    """Test 7 of the brief — a stale ready item is not silently reused."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="restage",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000107",
    )

    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert staged.final_text == "سلام مهرداد، قیمت جدید تلویزیون آماده است."

    campaign.template_text = TEMPLATE_B
    session.flush()

    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    session.expire_all()

    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert staged.final_text == "مهرداد عزیز، موجودی یخچال به‌روزرسانی شد."
    assert staged.queue_payload["final_text"] == staged.final_text


def test_queued_item_is_left_alone_when_template_changes(campaign_env):
    """Anything past 'ready' must never be rewritten under the worker's feet."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="queued-untouched",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000108",
    )

    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    original = staged.final_text
    staged.status = "queued"
    campaign.template_text = TEMPLATE_B
    session.flush()

    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    session.expire_all()

    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert staged.final_text == original


def test_contacts_come_from_campaign_recipients(campaign_env):
    """Test 8 — contacts attach via campaign_recipients, not Contact.campaign_id."""
    session, factory = campaign_env
    campaign, contact = factory(
        title="recipients",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000109",
    )

    assert contact.campaign_id is None, "fixture must mirror the importer"

    result = prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    assert result.total_contacts == 1
    assert result.ready_count == 1


def test_render_leaves_unknown_variables_untouched(campaign_env):
    """A typo in a variable name must be visible, not silently blanked."""
    session, factory = campaign_env
    campaign, contact = factory(
        title="unknown-var",
        template_text="سلام {{frist_name}}، خوش آمدید.",
        first_name="مهرداد",
        phone="+989120000110",
    )

    assert render_campaign_template(contact, campaign) == "سلام {{frist_name}}، خوش آمدید."


def test_all_three_name_variables_are_substituted(pg_session_factory):
    """first_name, last_name and full_name each resolve, including the derived one."""
    session = pg_session_factory()
    campaign = _make_campaign(
        session,
        title="vars",
        template_text="{{first_name}} | {{last_name}} | {{full_name}}",
    )
    contact = Contact(
        first_name="مهرداد",
        last_name="سعادت",
        phone="+989120000112",
        phone_e164="+989120000112",
        consent_status="allowed",
    )
    session.add(contact)
    session.flush()

    # full_name is NULL, so build_full_name must derive it from the parts.
    assert contact.full_name is None
    assert render_campaign_template(contact, campaign) == "مهرداد | سعادت | مهرداد سعادت"

    # An explicit full_name wins over the derived one.
    contact.full_name = "مهرداد س."
    assert render_campaign_template(contact, campaign) == "مهرداد | سعادت | مهرداد س."

    session.rollback()
    session.close()


def test_recipient_uniqueness_prevents_duplicate_contacts(campaign_env):
    """The CampaignRecipient join cannot fan out — the pair is unique."""
    from sqlalchemy.exc import IntegrityError

    session, factory = campaign_env
    campaign, contact = factory(
        title="no-dupes",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000113",
    )

    session.add(
        CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id)
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    result = prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    assert result.total_contacts == 1
    assert (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .count()
        == 1
    )


def test_staging_payload_stays_dry_run_only(campaign_env):
    """Preparing must never mark anything as pushed to a real queue."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="dry-only",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000114",
    )

    result = prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )

    assert result.redis_queue_pushed is False
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert staged.queue_payload["dry_run"] is True
    assert staged.queue_payload["safety_note"] == "DB staging only. Not pushed to Redis."


def test_mock_text_still_contains_the_marker(campaign_env):
    """Guard the placeholder itself so its shape cannot drift unnoticed."""
    session, factory = campaign_env
    campaign, contact = factory(
        title="marker",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000111",
    )
    assert DRY_RUN_MARKER in build_phase4_mock_final_text(contact, campaign)


# --- The request default itself ------------------------------------------
#
# Rendering the campaign's own text was fixed on the service side, but the
# request schema still defaulted to mock output, so a caller that omitted the
# flag kept getting the placeholder. These pin the default down.


def test_prepare_request_defaults_to_template_mode():
    """A request that says nothing must mean "use the campaign's text"."""
    assert PrepareMessagesRequest().force_mock_output is False
    assert PrepareMessagesRequest(limit=5).force_mock_output is False


def test_prepare_without_the_flag_renders_the_campaign_text(campaign_env):
    """Constructing the request with no flag goes down the template path."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="default-flag",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000112",
    )

    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest())

    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert staged.final_text == "سلام مهرداد، قیمت جدید تلویزیون آماده است."
    assert DRY_RUN_MARKER not in staged.final_text


def test_endpoint_with_empty_body_renders_the_campaign_text(campaign_env):
    """POST {} over HTTP — the shape the panel and curl actually send."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="empty-body",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000113",
    )

    response = client.post(
        f"/debug/campaigns/{campaign.id}/prepare-messages", json={}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["force_mock_output"] is False
    texts = [item["final_text"] for item in body["items"]]
    assert texts == ["سلام مهرداد، قیمت جدید تلویزیون آماده است."]
    assert all(DRY_RUN_MARKER not in text for text in texts)


def test_endpoint_with_empty_body_never_emits_the_reported_sentence(campaign_env):
    """The exact sentence from the field report must not come back."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="reported-sentence",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000114",
    )

    response = client.post(
        f"/debug/campaigns/{campaign.id}/prepare-messages", json={}
    )

    assert response.status_code == 200
    assert REPORTED_SENTENCE_FRAGMENT not in response.text
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert REPORTED_SENTENCE_FRAGMENT not in staged.final_text


def test_endpoint_with_explicit_flag_still_produces_mock(campaign_env):
    """Mock output stays reachable — it just has to be asked for."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="explicit-mock",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000115",
    )

    response = client.post(
        f"/debug/campaigns/{campaign.id}/prepare-messages",
        json={"force_mock_output": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["force_mock_output"] is True
    assert all(DRY_RUN_MARKER in item["final_text"] for item in body["items"])


def test_auto_prepare_never_asks_for_mock_output(campaign_env, monkeypatch):
    """Starting a campaign must not depend on the schema default at all."""
    session, factory = campaign_env
    campaign, _ = factory(
        title="auto-prepare",
        template_text=TEMPLATE_A,
        first_name="مهرداد",
        phone="+989120000116",
    )

    seen: list[PrepareMessagesRequest] = []

    def _spy(db, campaign_id, request):
        seen.append(request)
        return prepare_campaign_messages(db, campaign_id, request)

    monkeypatch.setattr(campaign_control, "prepare_campaign_messages", _spy)
    campaign_control._auto_prepare(session, campaign.id)

    assert len(seen) == 1
    assert seen[0].force_mock_output is False
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert DRY_RUN_MARKER not in staged.final_text
