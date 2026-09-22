"""PUT /campaigns/{id}/accounts must not prepare or enqueue messages.

Uses dedicated database mmp_pytest_campaign_accounts. Never writes to
mmp_isolated_readiness. Worker stays off and no real Redis queue is used.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from core_engine.config import get_settings
from tests.isolation import DEFAULT_PYTEST_DATABASE, assert_pytest_database_name
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    ConsentStatus,
    Contact,
    Message,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
)
from core_engine.services.campaign_footer import append_campaign_footer

AUTH = {"Authorization": "Bearer fake_token"}
ISOLATED_DB = DEFAULT_PYTEST_DATABASE
TEMPLATE = "سلام {{first_name}}، پیام تست."
APPROVED_PILOT_TEXT = "این یک پیام آزمایشی سامانه افرا پیام است. لطفاً نادیده بگیرید."


@pytest.fixture(autouse=True)
def _worker_and_queue_off(monkeypatch):
    monkeypatch.setenv("WORKER_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("REAL_MESSAGE_SENDING_ENABLED", "false")
    monkeypatch.setenv("REAL_QUEUE_PUSH_ENABLED", "false")
    monkeypatch.setenv("CHANNEL_CONNECTORS_ENABLED", "false")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.WORKER_EXECUTION_ENABLED is False
    assert settings.REAL_QUEUE_PUSH_ENABLED is False
    assert settings.REAL_MESSAGE_SENDING_ENABLED is False
    yield
    get_settings.cache_clear()


def _assert_isolated(session: Session) -> None:
    current = assert_pytest_database_name(
        str(session.execute(text("SELECT current_database()")).scalar())
    )
    assert current == ISOLATED_DB


def _phone() -> str:
    return f"+98912{uuid.uuid4().int % 10_000_000:07d}"


def _account(session: Session, *, label: str | None = None) -> Account:
    account = Account(
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label=label or f"put-acc-{uuid.uuid4().hex[:8]}",
        phone_number=_phone().lstrip("+"),
    )
    session.add(account)
    session.flush()
    return account


def _account_with_id(session: Session, account_id: int) -> Account:
    existing = session.get(Account, account_id)
    if existing is not None:
        existing.platform = PlatformType.RUBIKA
        existing.status = AccountStatus.ACTIVE
        existing.archived_at = None
        existing.label = existing.label or f"put-acc-{account_id}"
        session.flush()
        return existing
    account = Account(
        id=account_id,
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label=f"put-acc-{account_id}",
        phone_number=_phone().lstrip("+"),
    )
    session.add(account)
    session.flush()
    session.execute(
        text(
            "SELECT setval(pg_get_serial_sequence('accounts', 'id'), "
            "(SELECT COALESCE(MAX(id), 1) FROM accounts))"
        )
    )
    return account


def _contact(session: Session) -> Contact:
    phone = _phone()
    contact = Contact(
        first_name="علی",
        phone=phone,
        phone_e164=phone,
        consent_status=ConsentStatus.ALLOWED.value,
    )
    session.add(contact)
    session.flush()
    return contact


def _draft_campaign(
    session: Session,
    *,
    with_recipient: bool = True,
    template_text: str = TEMPLATE,
) -> Campaign:
    campaign = Campaign(
        name=f"put-no-prep-{uuid.uuid4().hex[:8]}",
        title="put-no-prep",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        template_text=template_text,
        use_gpt=False,
        include_products=False,
        status=CampaignStatus.DRAFT.value,
    )
    session.add(campaign)
    session.flush()
    if with_recipient:
        session.add(
            CampaignRecipient(campaign_id=campaign.id, contact_id=_contact(session).id)
        )
    session.commit()
    return campaign


def _counts(session: Session, campaign_id: int) -> dict[str, int]:
    return {
        "messages": session.query(Message).filter(Message.campaign_id == campaign_id).count(),
        "rendered": session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign_id)
        .count(),
        "queue": session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .count(),
        "accounts": session.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == campaign_id)
        .count(),
    }


def _assert_not_prepared(body: dict, session: Session, campaign: Campaign) -> None:
    auto = body.get("auto_prepare") or {}
    assert auto.get("attempted") is False
    assert auto.get("prepared") is False
    assert auto.get("skipped") is True
    assert auto.get("skip_reason") == "accounts_update_does_not_prepare"
    session.expire_all()
    row = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert row.status == CampaignStatus.DRAFT.value
    counts = _counts(session, campaign.id)
    assert counts["messages"] == 0
    assert counts["rendered"] == 0
    assert counts["queue"] == 0


def test_put_accounts_does_not_prepare_draft(client, pg_session_factory):
    session = pg_session_factory()
    _assert_isolated(session)
    account = _account(session)
    campaign = _draft_campaign(session)

    response = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [account.id]},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account_ids"] == [account.id]
    _assert_not_prepared(body, session, campaign)
    assert _counts(session, campaign.id)["accounts"] == 1


def test_put_accounts_duplicate_is_idempotent(client, pg_session_factory):
    session = pg_session_factory()
    _assert_isolated(session)
    account = _account(session)
    campaign = _draft_campaign(session)
    payload = {"account_ids": [account.id]}

    first = client.put(f"/campaigns/{campaign.id}/accounts", json=payload, headers=AUTH)
    second = client.put(f"/campaigns/{campaign.id}/accounts", json=payload, headers=AUTH)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    _assert_not_prepared(second.json(), session, campaign)
    assert _counts(session, campaign.id)["accounts"] == 1


def test_put_accounts_replace_changes_only_campaign_accounts(client, pg_session_factory):
    session = pg_session_factory()
    _assert_isolated(session)
    first = _account(session, label="first")
    second = _account(session, label="second")
    campaign = _draft_campaign(session)

    created = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [first.id]},
        headers=AUTH,
    )
    assert created.status_code == 200, created.text
    replaced = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [second.id]},
        headers=AUTH,
    )
    assert replaced.status_code == 200, replaced.text
    _assert_not_prepared(replaced.json(), session, campaign)
    links = (
        session.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == campaign.id)
        .all()
    )
    assert [link.account_id for link in links] == [second.id]


def test_put_automatic_accounts_does_not_prepare(client, pg_session_factory):
    session = pg_session_factory()
    _assert_isolated(session)
    campaign = _draft_campaign(session)
    response = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": []},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    _assert_not_prepared(response.json(), session, campaign)


def test_explicit_prepare_still_works_after_put_accounts(client, pg_session_factory):
    session = pg_session_factory()
    _assert_isolated(session)
    account = _account(session)
    campaign = _draft_campaign(session)

    assigned = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [account.id]},
        headers=AUTH,
    )
    assert assigned.status_code == 200, assigned.text
    _assert_not_prepared(assigned.json(), session, campaign)

    prepared = client.post(
        f"/campaigns/{campaign.id}/prepare",
        json={},
        headers=AUTH,
    )
    assert prepared.status_code == 200, prepared.text
    session.expire_all()
    row = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert row.status == CampaignStatus.PREPARED.value
    counts = _counts(session, campaign.id)
    assert counts["messages"] >= 1
    assert counts["rendered"] >= 1
    assert counts["queue"] >= 1
    assert counts["accounts"] == 1


def test_prepare_without_campaign_account_creates_no_artifacts(client, pg_session_factory):
    session = pg_session_factory()
    _assert_isolated(session)
    _account(session)
    campaign = _draft_campaign(session)

    prepared = client.post(
        f"/campaigns/{campaign.id}/prepare",
        json={},
        headers=AUTH,
    )
    assert prepared.status_code == 400, prepared.text
    session.expire_all()
    row = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert row.status == CampaignStatus.DRAFT.value
    counts = _counts(session, campaign.id)
    assert counts["messages"] == 0
    assert counts["rendered"] == 0
    assert counts["queue"] == 0


def test_prepare_after_replacing_sender_174_uses_only_197(client, pg_session_factory):
    session = pg_session_factory()
    _assert_isolated(session)
    stale = _account_with_id(session, 174)
    ready = _account_with_id(session, 197)
    campaign = _draft_campaign(session)
    assert stale.id == 174
    assert ready.id == 197

    first = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [174]},
        headers=AUTH,
    )
    assert first.status_code == 200, first.text
    _assert_not_prepared(first.json(), session, campaign)
    assert first.json()["account_ids"] == [174]

    replaced = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [197]},
        headers=AUTH,
    )
    assert replaced.status_code == 200, replaced.text
    _assert_not_prepared(replaced.json(), session, campaign)
    assert replaced.json()["account_ids"] == [197]

    prepared = client.post(
        f"/campaigns/{campaign.id}/prepare",
        json={},
        headers=AUTH,
    )
    assert prepared.status_code == 200, prepared.text
    session.expire_all()
    messages = (
        session.query(Message).filter(Message.campaign_id == campaign.id).all()
    )
    assert [message.account_id for message in messages] == [197]
    assert session.query(Message).filter(Message.account_id == 174).count() == 0
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .all()
    )
    assert staged
    assert all(item.queue_payload.get("account_id") == 197 for item in staged)
    links = (
        session.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == campaign.id)
        .all()
    )
    assert [link.account_id for link in links] == [197]


def test_put_accounts_resyncs_stale_message_from_174_to_197(client, pg_session_factory):
    session = pg_session_factory()
    _assert_isolated(session)
    _account_with_id(session, 174)
    _account_with_id(session, 197)
    campaign = _draft_campaign(session)

    assigned = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [174]},
        headers=AUTH,
    )
    assert assigned.status_code == 200, assigned.text
    prepared = client.post(
        f"/campaigns/{campaign.id}/prepare",
        json={},
        headers=AUTH,
    )
    assert prepared.status_code == 200, prepared.text
    session.expire_all()
    assert [
        row.account_id
        for row in session.query(Message).filter(Message.campaign_id == campaign.id)
    ] == [174]

    replaced = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [197]},
        headers=AUTH,
    )
    assert replaced.status_code == 200, replaced.text
    auto = replaced.json().get("auto_prepare") or {}
    assert auto.get("attempted") is False
    assert auto.get("prepared") is False
    assert auto.get("skip_reason") == "accounts_update_does_not_prepare"
    session.expire_all()
    row = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert row.status == CampaignStatus.PREPARED.value
    messages = (
        session.query(Message).filter(Message.campaign_id == campaign.id).all()
    )
    assert [message.account_id for message in messages] == [197]
    counts = _counts(session, campaign.id)
    assert counts["messages"] == 1
    assert counts["rendered"] == 1
    assert counts["queue"] == 1
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .one()
    )
    assert staged.queue_payload.get("account_id") == 197


def _assign_197_and_prepare(client, campaign_id: int):
    assigned = client.put(
        f"/campaigns/{campaign_id}/accounts",
        json={"account_ids": [197]},
        headers=AUTH,
    )
    assert assigned.status_code == 200, assigned.text
    prepared = client.post(
        f"/campaigns/{campaign_id}/prepare",
        json={},
        headers=AUTH,
    )
    assert prepared.status_code == 200, prepared.text
    return prepared


def _assert_message_bound_to_197(session: Session, campaign_id: int) -> StagedQueueItem:
    messages = session.query(Message).filter(Message.campaign_id == campaign_id).all()
    assert [message.account_id for message in messages] == [197]
    links = (
        session.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == campaign_id)
        .all()
    )
    assert [link.account_id for link in links] == [197]
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .one()
    )
    assert staged.queue_payload.get("account_id") == 197
    return staged


def test_prepare_footer_off_matches_approved_62_char_text(
    client, pg_session_factory, monkeypatch
):
    monkeypatch.setenv("CAMPAIGN_FOOTER_ENABLED", "false")
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    session = pg_session_factory()
    _assert_isolated(session)
    _account_with_id(session, 197)
    campaign = _draft_campaign(session, template_text=APPROVED_PILOT_TEXT)

    _assign_197_and_prepare(client, campaign.id)
    session.expire_all()
    staged = _assert_message_bound_to_197(session, campaign.id)
    assert len(APPROVED_PILOT_TEXT) == 62
    assert staged.final_text == APPROVED_PILOT_TEXT
    assert len(staged.final_text) == 62
    assert staged.queue_payload.get("dry_run") is False


def test_prepare_footer_on_keeps_previous_append_behavior(
    client, pg_session_factory, monkeypatch
):
    monkeypatch.setenv("CAMPAIGN_FOOTER_ENABLED", "true")
    monkeypatch.setenv("CAMPAIGN_CONTACT_PHONE", "02174393000")
    monkeypatch.setenv("CAMPAIGN_WHATSAPP_CHANNEL_URL", "https://whatsapp.example/channel")
    monkeypatch.setenv("CAMPAIGN_TELEGRAM_CHANNEL_URL", "https://t.me/example")
    monkeypatch.setenv("CAMPAIGN_RUBIKA_CHANNEL_URL", "https://rubika.ir/example")
    monkeypatch.setenv("CAMPAIGN_BALE_CHANNEL_URL", "https://ble.ir/example")
    monkeypatch.setenv("CAMPAIGN_SOROUSH_CHANNEL_URL", "https://splus.ir/example")
    monkeypatch.setenv("DRY_RUN", "true")
    get_settings.cache_clear()
    session = pg_session_factory()
    _assert_isolated(session)
    _account_with_id(session, 197)
    campaign = _draft_campaign(session, template_text=APPROVED_PILOT_TEXT)

    _assign_197_and_prepare(client, campaign.id)
    session.expire_all()
    staged = _assert_message_bound_to_197(session, campaign.id)
    expected = append_campaign_footer(APPROVED_PILOT_TEXT)
    assert expected != APPROVED_PILOT_TEXT
    assert staged.final_text == expected
    assert staged.final_text.startswith(APPROVED_PILOT_TEXT)
    assert staged.queue_payload.get("dry_run") is True


def test_prepare_dry_run_true_stages_experimental_queue(
    client, pg_session_factory, monkeypatch
):
    monkeypatch.setenv("CAMPAIGN_FOOTER_ENABLED", "false")
    monkeypatch.setenv("DRY_RUN", "true")
    get_settings.cache_clear()
    session = pg_session_factory()
    _assert_isolated(session)
    _account_with_id(session, 197)
    campaign = _draft_campaign(session, template_text=APPROVED_PILOT_TEXT)

    _assign_197_and_prepare(client, campaign.id)
    session.expire_all()
    staged = _assert_message_bound_to_197(session, campaign.id)
    assert staged.final_text == APPROVED_PILOT_TEXT
    assert staged.queue_payload.get("dry_run") is True
