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

AUTH = {"Authorization": "Bearer fake_token"}
ISOLATED_DB = DEFAULT_PYTEST_DATABASE
TEMPLATE = "سلام {{first_name}}، پیام تست."


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


def _draft_campaign(session: Session, *, with_recipient: bool = True) -> Campaign:
    campaign = Campaign(
        name=f"put-no-prep-{uuid.uuid4().hex[:8]}",
        title="put-no-prep",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        template_text=TEMPLATE,
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
