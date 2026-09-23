"""Reprepare gates and uncapped audience order. No live send."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core_engine.models import (
    CampaignRecipient,
    CampaignStatus,
    Message,
    MessageAttempt,
    MessageAttemptStatus,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_production_guards import (
    campaign_cap_view,
    resolve_campaign_max_total_messages,
)
from core_engine.services.campaign_reprepare import ReprepareRejected, reprepare_campaign
from core_engine.services.phase4_prepare import dedupe_recipient_rows, prepare_campaign_messages
from tests.core_engine.test_production_safety_guards import (
    _campaign,
    _contact,
    _make_account,
)

ROOT = Path(__file__).resolve().parents[2]


def test_null_cap_semantics_and_no_hidden_default_five():
    assert resolve_campaign_max_total_messages(SimpleNamespace(max_contacts=None)) is None
    view = campaign_cap_view(SimpleNamespace(max_contacts=None))
    assert view == {"effective_cap": None, "unlimited": True}
    guards = (ROOT / "core_engine" / "services" / "campaign_production_guards.py").read_text(
        encoding="utf-8"
    )
    config = (ROOT / "core_engine" / "config.py").read_text(encoding="utf-8")
    page = (ROOT.parent / "frontend" / "src" / "pages" / "campaigns" / "[id].tsx").read_text(
        encoding="utf-8"
    )
    assert "controlled_production_default_max" not in guards
    assert "CONTROLLED_PRODUCTION_DEFAULT_MAX_TOTAL_MESSAGES" not in config
    assert "بدون سقف کل" in (
        ROOT.parent / "frontend" / "locales" / "fa" / "common.json"
    ).read_text(encoding="utf-8")
    assert "campaignUnlimitedTotalCap" in page
    capacity = (ROOT / "core_engine" / "services" / "campaign_capacity.py").read_text(
        encoding="utf-8"
    )
    operations = (ROOT / "core_engine" / "services" / "rubika_operations.py").read_text(
        encoding="utf-8"
    )
    assert "hourly_count_cap" in operations
    assert "daily_count_cap" in operations
    assert "_hourly_unlimited" in capacity
    assert "hourly_message_limit" not in (
        ROOT / "core_engine" / "services" / "campaign_reprepare.py"
    ).read_text(encoding="utf-8")


def test_dedupe_keeps_first_seen_order():
    rows = [
        (SimpleNamespace(id=1), SimpleNamespace(id=10)),
        (SimpleNamespace(id=2), SimpleNamespace(id=10)),
        (SimpleNamespace(id=3), SimpleNamespace(id=11)),
    ]
    unique = dedupe_recipient_rows(rows)
    assert [contact.id for _recipient, contact in unique] == [10, 11]
    assert [recipient.id for recipient, _contact in unique] == [1, 3]


def _quiet(campaign_id: int) -> str | None:
    return None


def test_null_max_contacts_prepares_every_recipient(pg_session_factory, monkeypatch):
    """Controlled production must not invent a campaign-wide cap of 5."""
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "true")
    from core_engine.config import get_settings

    get_settings.cache_clear()
    session = pg_session_factory()
    account = _make_account(session, label="uncapped")
    contacts = [_contact(session, valid=True, first_name=f"U{i}") for i in range(6)]
    campaign = _campaign(session, account, contacts, template="متن ثابت", max_contacts=None)
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    prepared = session.query(Message).filter(Message.campaign_id == campaign.id).count()
    assert prepared == 6
    view = campaign_cap_view(campaign)
    assert view["effective_cap"] is None
    assert view["unlimited"] is True


def test_reprepare_resets_unsent_rows_and_keeps_order(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="reprepare")
    contacts = [_contact(session, valid=True, first_name=f"R{i}") for i in range(4)]
    campaign = _campaign(
        session, account, contacts, template="متن ثابت", max_contacts=2
    )
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    ready_before = (
        session.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign.id,
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .order_by(StagedQueueItem.id.asc())
        .all()
    )
    assert len(ready_before) == 2
    first_contacts = [int(item.contact_id) for item in ready_before]
    first_texts = [item.final_text for item in ready_before]
    first_accounts = [
        int(item.queue_payload["account_id"]) for item in ready_before
    ]

    campaign.max_contacts = None
    campaign.status = CampaignStatus.PREPARED.value
    session.commit()

    result = reprepare_campaign(session, campaign.id, activity_probe=_quiet)
    assert result["reset_unsent_staged"] == 2
    assert result["ready_count"] == 4
    assert result["redis_queue_pushed"] is False

    ready_after = (
        session.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign.id,
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .order_by(StagedQueueItem.id.asc())
        .all()
    )
    ordered_contacts = [int(item.contact_id) for item in ready_after]
    assert ordered_contacts[:2] == first_contacts
    assert [item.final_text for item in ready_after[:2]] == first_texts
    assert [int(item.queue_payload["account_id"]) for item in ready_after[:2]] == first_accounts
    assert len({item.contact_id for item in ready_after}) == 4


def test_reprepare_rejects_running_campaign(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="running")
    contact = _contact(session, valid=True)
    campaign = _campaign(session, account, [contact], template="متن ثابت")
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    with pytest.raises(ReprepareRejected) as caught:
        reprepare_campaign(session, campaign.id, activity_probe=_quiet)
    assert caught.value.code == "CAMPAIGN_RUNNING"
    still = session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).count()
    assert still == 1


def test_reprepare_rejects_queued_staged(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="queued")
    contact = _contact(session, valid=True)
    campaign = _campaign(session, account, [contact], template="متن ثابت")
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    item = session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).one()
    item.status = StagedQueueItemStatus.QUEUED.value
    session.commit()
    with pytest.raises(ReprepareRejected) as caught:
        reprepare_campaign(session, campaign.id, activity_probe=_quiet)
    assert caught.value.code == "CAMPAIGN_QUEUE_NONEMPTY"


def test_reprepare_rejects_live_lease_or_inflight(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="lease")
    contact = _contact(session, valid=True)
    campaign = _campaign(session, account, [contact], template="متن ثابت")
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    campaign.status = CampaignStatus.PREPARED.value
    session.commit()
    with pytest.raises(ReprepareRejected) as caught:
        reprepare_campaign(
            session,
            campaign.id,
            activity_probe=lambda _campaign_id: "lease",
        )
    assert caught.value.code == "CAMPAIGN_LIVE_ACTIVITY"
    assert session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).count() == 1


def test_reprepare_rejects_attempts(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="attempt")
    contact = _contact(session, valid=True)
    campaign = _campaign(session, account, [contact], template="متن ثابت")
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    message = session.query(Message).filter_by(campaign_id=campaign.id).one()
    session.add(
        MessageAttempt(
            message_id=message.id,
            attempt_no=1,
            status=MessageAttemptStatus.SUCCESS,
        )
    )
    campaign.status = CampaignStatus.PREPARED.value
    session.commit()
    with pytest.raises(ReprepareRejected) as caught:
        reprepare_campaign(session, campaign.id, activity_probe=_quiet)
    assert caught.value.code == "CAMPAIGN_HAS_ATTEMPTS"


def test_reprepare_rejects_successful_send(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="sent")
    contact = _contact(session, valid=True)
    campaign = _campaign(session, account, [contact], template="متن ثابت")
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    recipient = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .one()
    )
    recipient.send_status = SendStatus.DELIVERED
    campaign.status = CampaignStatus.PAUSED.value
    session.commit()
    with pytest.raises(ReprepareRejected) as caught:
        reprepare_campaign(session, campaign.id, activity_probe=_quiet)
    assert caught.value.code == "CAMPAIGN_HAS_SENDS"
