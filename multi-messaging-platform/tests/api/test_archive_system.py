"""Archive system — accounts + campaigns (isolated, no provider sends)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import (
    Account,
    AccountStatus,
    AuditLog,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    PlatformType,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.archive import (
    ERROR_ACCOUNT_ARCHIVED,
    ERROR_CAMPAIGN_ARCHIVED,
    archive_account,
    archive_campaign,
    restore_account,
    restore_campaign,
)
from core_engine.services.campaign_auto_prepare import try_auto_prepare_campaign
from core_engine.services.campaign_sender_eligibility import (
    evaluate_campaign_sender_eligibility,
)

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def archive_account_row(pg_session_factory):
    s = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="+989120000001",
        label="archive-test-acct",
        status=AccountStatus.ACTIVE,
    )
    s.add(account)
    s.commit()
    s.refresh(account)
    yield account, s
    s.query(AuditLog).filter(AuditLog.resource_id == str(account.id)).delete()
    s.query(Account).filter(Account.id == account.id).delete()
    s.commit()
    s.close()


@pytest.fixture
def archive_campaign_row(pg_session_factory):
    s = pg_session_factory()
    campaign = Campaign(
        name="archive-test-camp",
        title="archive-test-camp",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.PREPARED.value,
        template_text="hi",
    )
    s.add(campaign)
    s.commit()
    s.refresh(campaign)
    yield campaign, s
    s.query(AuditLog).filter(AuditLog.resource_id == str(campaign.id)).delete()
    s.query(StagedQueueItem).filter(StagedQueueItem.campaign_id == campaign.id).delete()
    s.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign.id).delete()
    s.query(Campaign).filter(Campaign.id == campaign.id).delete()
    s.commit()
    s.close()


def test_archive_active_account_api(archive_account_row, admin_auth):
    account, s = archive_account_row
    r = client.post(f"/accounts/{account.id}/archive", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["already_archived"] is False
    assert body["archived_at"] is not None
    s.refresh(account)
    assert account.archived_at is not None


def test_archive_already_archived_idempotent(archive_account_row, admin_auth):
    account, s = archive_account_row
    archive_account(s, account, actor="t")
    s.commit()
    r1 = client.post(f"/accounts/{account.id}/archive", headers=AUTH)
    r2 = client.post(f"/accounts/{account.id}/archive", headers=AUTH)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["already_archived"] is True
    assert r2.json()["already_archived"] is True


def test_restore_account_api(archive_account_row, admin_auth):
    account, s = archive_account_row
    archive_account(s, account, actor="t")
    s.commit()
    r = client.post(f"/accounts/{account.id}/restore", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["already_active"] is False
    s.refresh(account)
    assert account.archived_at is None


def test_archived_excluded_from_active_list(archive_account_row, admin_auth):
    account, s = archive_account_row
    archive_account(s, account, actor="t")
    s.commit()
    active = client.get("/accounts", headers=AUTH).json()
    assert all(int(i["id"]) != int(account.id) for i in active["items"])
    archived = client.get("/accounts?archived=true", headers=AUTH).json()
    assert any(int(i["id"]) == int(account.id) for i in archived["items"])


def test_archived_account_not_sender_eligible(archive_account_row):
    account, s = archive_account_row
    archive_account(s, account, actor="t")
    s.commit()
    s.refresh(account)
    runtime = MagicMock()
    runtime.enabled = True
    runtime.runtime_status = "READY"
    runtime.runtime_status_label = "آماده"
    runtime.worker_covered = True
    runtime.dispatch_ready = True
    runtime.dispatch_blocker = None
    runtime.last_verified_at = None
    elig = evaluate_campaign_sender_eligibility(s, account, runtime=runtime)
    assert elig.campaign_eligible is False
    assert elig.blocker_code == "ACCOUNT_ARCHIVED"


def test_archive_campaign_prepared(archive_campaign_row):
    campaign, s = archive_campaign_row
    r = client.post(f"/campaigns/{campaign.id}/archive", headers=AUTH)
    assert r.status_code == 200
    s.refresh(campaign)
    assert campaign.archived_at is not None


def test_archive_campaign_running_stops_future(archive_campaign_row, pg_session_factory):
    campaign, s = archive_campaign_row
    campaign.status = CampaignStatus.RUNNING.value
    contact_id = None
    from core_engine.models import Contact

    contact = Contact(phone="+989121111111", consent_status="allowed")
    s.add(contact)
    s.flush()
    contact_id = contact.id
    recip = CampaignRecipient(
        campaign_id=campaign.id,
        contact_id=contact.id,
        send_status=SendStatus.PENDING.value,
    )
    s.add(recip)
    staged = StagedQueueItem(
        campaign_id=campaign.id,
        contact_id=contact.id,
        channel="bale",
        status=StagedQueueItemStatus.READY.value,
        final_text="x",
        queue_payload={"account_id": 1, "campaign_id": campaign.id},
    )
    s.add(staged)
    s.commit()

    result = archive_campaign(s, campaign, actor="t")
    s.commit()
    assert result.queued_items_cancelled >= 1
    assert result.pending_recipients_stopped >= 1
    s.refresh(campaign)
    assert campaign.archived_at is not None
    assert campaign.status == CampaignStatus.PAUSED.value
    s.refresh(staged)
    assert staged.status == StagedQueueItemStatus.SKIPPED.value
    assert staged.skip_reason == ERROR_CAMPAIGN_ARCHIVED
    s.refresh(recip)
    assert recip.send_status == SendStatus.FAILED_PERMANENT.value
    assert recip.failure_reason == ERROR_CAMPAIGN_ARCHIVED

    s.query(StagedQueueItem).filter(StagedQueueItem.id == staged.id).delete()
    s.query(CampaignRecipient).filter(CampaignRecipient.id == recip.id).delete()
    s.query(Contact).filter(Contact.id == contact_id).delete()
    s.commit()


def test_archived_campaign_start_blocked(archive_campaign_row):
    campaign, s = archive_campaign_row
    archive_campaign(s, campaign, actor="t")
    s.commit()
    r = client.post(f"/campaigns/{campaign.id}/start", headers=AUTH, json={})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == ERROR_CAMPAIGN_ARCHIVED


def test_archived_auto_prepare_blocked(archive_campaign_row):
    campaign, s = archive_campaign_row
    campaign.status = CampaignStatus.DRAFT.value
    archive_campaign(s, campaign, actor="t")
    s.commit()
    result = try_auto_prepare_campaign(s, campaign.id, trigger="test")
    assert result.skipped is True
    assert result.skip_reason == "campaign_archived"


def test_campaign_restore_no_auto_start(archive_campaign_row):
    campaign, s = archive_campaign_row
    campaign.status = CampaignStatus.RUNNING.value
    result = archive_campaign(s, campaign, actor="t")
    s.commit()
    assert campaign.status == CampaignStatus.PAUSED.value
    restore_campaign(s, campaign, actor="t")
    s.commit()
    s.refresh(campaign)
    assert campaign.archived_at is None
    assert campaign.status != CampaignStatus.RUNNING.value


def test_restore_campaign_idempotent_active(archive_campaign_row):
    campaign, s = archive_campaign_row
    r = client.post(f"/campaigns/{campaign.id}/restore", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["already_active"] is True


def test_archived_hidden_from_campaign_list(archive_campaign_row):
    campaign, s = archive_campaign_row
    archive_campaign(s, campaign, actor="t")
    s.commit()
    active = client.get("/campaigns?limit=100", headers=AUTH).json()
    assert all(int(i["id"]) != int(campaign.id) for i in active["items"])
    archived = client.get("/campaigns?archived=true&limit=100", headers=AUTH).json()
    assert any(int(i["id"]) == int(campaign.id) for i in archived["items"])


def test_worker_archive_guard_blocks_send(pg_session_factory):
    s = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="+989120000099",
        label="archive-guard-acct",
        status=AccountStatus.ACTIVE,
    )
    campaign = Campaign(
        name="archive-guard-camp",
        title="archive-guard-camp",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.PREPARED.value,
        template_text="hi",
    )
    s.add_all([account, campaign])
    s.commit()
    s.refresh(account)
    s.refresh(campaign)
    aid, cid = int(account.id), int(campaign.id)

    archive_account(s, account, actor="t")
    archive_campaign(s, campaign, actor="t")
    s.commit()
    s.refresh(account)
    s.refresh(campaign)
    assert account.archived_at is not None
    assert campaign.archived_at is not None

    from workers.db import check_archive_blocks_provider_send

    s.expire_all()
    assert check_archive_blocks_provider_send(
        account_id=aid, campaign_id=None, db=s
    ) == "account_archived"
    assert check_archive_blocks_provider_send(
        account_id=None, campaign_id=cid, db=s
    ) == "campaign_archived"

    account = s.query(Account).filter(Account.id == aid).one()
    restore_account(s, account, actor="t")
    s.commit()
    s.expire_all()
    assert (
        check_archive_blocks_provider_send(
            account_id=aid, campaign_id=cid, db=s
        )
        == "campaign_archived"
    )

    s.query(AuditLog).filter(AuditLog.resource_id.in_([str(aid), str(cid)])).delete(
        synchronize_session=False
    )
    s.query(Campaign).filter(Campaign.id == cid).delete()
    s.query(Account).filter(Account.id == aid).delete()
    s.commit()
    s.close()


@pytest.mark.asyncio
async def test_delivery_refuses_archived_before_connector(
    archive_account_row, monkeypatch
):
    account, s = archive_account_row
    archive_account(s, account, actor="t")
    s.commit()

    from workers.config import WorkerSettings
    from workers.delivery import deliver_platform_message
    from workers.payloads import WorkerPayload

    settings = MagicMock(spec=WorkerSettings)
    settings.DRY_RUN = False
    settings.SHADOW_MODE = False
    settings.REAL_MESSAGE_SENDING_ENABLED = True
    settings.CHANNEL_CONNECTORS_ENABLED = True

    called = {"n": 0}

    async def _should_not_run(*_a, **_k):
        called["n"] += 1
        raise AssertionError("connector must not run for archived account")

    monkeypatch.setattr(
        "workers.delivery.deliver_bale_live",
        _should_not_run,
    )

    payload = WorkerPayload(
        message_id=1,
        campaign_id=999001,
        contact_id=1,
        account_id=account.id,
        platform="bale",
        recipient="+98912",
        recipient_type="phone",
        message_text="x",
        dedupe_key="archive-test-dedupe",
    )
    # campaign 999001 missing → fail closed as campaign_archived OR account first
    result = await deliver_platform_message("bale", payload, settings)
    assert result.success is False
    assert result.retryable is False
    assert result.error_code in {"account_archived", "campaign_archived"}
    assert called["n"] == 0


def test_account_archive_cancels_staged_for_account(archive_account_row, archive_campaign_row):
    account, s = archive_account_row
    campaign, _ = archive_campaign_row
    from core_engine.models import Contact

    contact = Contact(phone="+989122222222", consent_status="allowed")
    s.add(contact)
    s.flush()
    staged = StagedQueueItem(
        campaign_id=campaign.id,
        contact_id=contact.id,
        channel="bale",
        status=StagedQueueItemStatus.READY.value,
        final_text="x",
        queue_payload={"account_id": int(account.id), "campaign_id": int(campaign.id)},
    )
    s.add(staged)
    s.commit()
    result = archive_account(s, account, actor="t")
    s.commit()
    assert result.queued_items_cancelled == 1
    s.refresh(staged)
    assert staged.skip_reason == ERROR_ACCOUNT_ARCHIVED
    s.query(StagedQueueItem).filter(StagedQueueItem.id == staged.id).delete()
    s.query(Contact).filter(Contact.id == contact.id).delete()
    s.commit()
