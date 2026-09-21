"""Canonical automatic Rubika CampaignAccount assignment.

Does not start the worker, prepare campaigns, or create live sends.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func

from core_engine.config import get_settings
from core_engine.main import app
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    ImportBatch,
    ImportStatus,
    Message,
    PlatformType,
    RenderStatus,
    RubikaAccountActivation,
    RubikaAccountActivationState,
    RubikaAccountPool,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
from core_engine.services.campaign_sender_assignment import (
    backfill_automatic_rubika_campaign_senders,
    campaign_counts,
    classify_campaign_for_automatic_backfill,
    ensure_automatic_campaign_sender_assignments,
    list_stable_assignment_eligible_accounts,
)
from core_engine.services.rubika_circuit import RubikaCircuitSnapshot
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.config import get_worker_settings

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}
IRAN = ZoneInfo("Asia/Tehran")


@pytest.fixture(autouse=True)
def _assignment_env(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_MANAGER_ACTIVATION_REQUIRED", "true")
    monkeypatch.setenv("DEFAULT_RUBIKA_POOL", "day")
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setattr(
        "workers.rubika_account_pool.resolve_current_phase",
        lambda db, clock=None: "day",
    )
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


def _rest_foreign_rubika(session):
    for row in (
        session.query(Account)
        .filter(Account.platform == PlatformType.RUBIKA)
        .all()
    ):
        row.status = AccountStatus.RESTING
        if row.archived_at is None:
            row.archived_at = datetime.now(timezone.utc)
    session.commit()


def _stable_account(
    session,
    *,
    phase: str = "day",
    status: AccountStatus = AccountStatus.ACTIVE,
    archived: bool = False,
    with_session: bool = True,
    identity: bool = True,
    activation: RubikaAccountActivationState | None = RubikaAccountActivationState.CONFIRMED,
    in_pool: bool = True,
) -> Account:
    token = uuid.uuid4().hex[:10]
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98912{abs(hash(token)) % 10_000_000:07d}",
        label=f"assign-{token}",
        status=status,
        warming_started_at=datetime.now(IRAN) - timedelta(days=20),
    )
    if archived:
        account.archived_at = datetime.now(timezone.utc)
    guid = f"guid-{token}"
    if identity:
        account.rubika_guid = guid
        account.rubika_identity_status = RubikaIdentityStatus.VERIFIED
        account.rubika_identity_verified_at = datetime.now(timezone.utc)
    session.add(account)
    session.flush()
    if with_session:
        envelope = build_session_envelope(
            phone_number=account.phone_number,
            auth="d" * 32,
            guid=guid,
            user_agent="ua",
            private_key="-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----",
        )
        store_channel_session(
            session,
            account_id=account.id,
            session_type=SessionType.RUBIKA_SESSION,
            plaintext=envelope,
            session_status=RubikaSessionStatus.ACTIVE,
            identity_guid=guid,
        )
    if in_pool:
        session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
    if activation is not None:
        session.add(
            RubikaAccountActivation(
                account_id=account.id,
                state=activation,
                confirmed_at=(
                    datetime.now(timezone.utc)
                    if activation
                    in {
                        RubikaAccountActivationState.CONFIRMED,
                        RubikaAccountActivationState.READY_TO_SEND,
                    }
                    else None
                ),
            )
        )
    session.commit()
    session.refresh(account)
    return account


def _draft_campaign(
    session,
    *,
    platform: PlatformType = PlatformType.RUBIKA,
    status: str = CampaignStatus.DRAFT.value,
    recipients: int = 2,
    archived: bool = False,
) -> Campaign:
    campaign = Campaign(
        name=f"assign-{uuid.uuid4().hex[:8]}",
        title="assignment",
        channel=platform.value,
        platform=platform,
        status=status,
        template_text="سلام",
    )
    if archived:
        campaign.archived_at = datetime.now(timezone.utc)
    session.add(campaign)
    session.flush()
    for index in range(recipients):
        suffix = f"{abs(hash(uuid.uuid4().hex)) % 1_000_000_000:09d}{index}"
        contact = Contact(
            first_name=f"c{index}",
            phone=f"+989{suffix[:9]}",
            phone_e164=f"+989{uuid.uuid4().int % 10**10:010d}",
            consent_status="allowed",
            campaign_id=campaign.id,
        )
        session.add(contact)
        session.flush()
        session.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                contact_id=contact.id,
                render_status=RenderStatus.PENDING,
                send_status=SendStatus.PENDING,
            )
        )
    session.commit()
    session.refresh(campaign)
    return campaign


def _counts(session, campaign_id: int) -> dict[str, int]:
    return campaign_counts(session, campaign_id)


def _install_worker_off(monkeypatch):
    async def fake_circuit(*_a, **_k):
        return RubikaCircuitSnapshot(
            state="closed",
            opened_at=None,
            half_open_at=None,
            open_until=None,
            probe_budget=1,
            probe_remaining=0,
            systemic_account_count=0,
            systemic_failure_count=0,
            reason=None,
        )

    async def fake_none(*_a, **_k):
        return None

    async def fake_pf(*_a, **_k):
        return SimpleNamespace(code="READY", details={})

    async def fake_health(*_a, **_k):
        return SimpleNamespace(health_state="healthy")

    async def fake_quota(*_a, **_k):
        return SimpleNamespace(
            cooldown_until=None,
            throttle_active=False,
            sent_today=0,
            sent_this_hour=0,
            delay_ttl_seconds=0,
        )

    async def fake_coverage(*_a, **_k):
        return False

    class PingRedis:
        async def ping(self):
            return True

        async def get(self, *_a, **_k):
            return None

        async def set(self, *_a, **_k):
            return True

        async def delete(self, *_a, **_k):
            return 0

        async def scard(self, *_a, **_k):
            return 0

        async def exists(self, *_a, **_k):
            return 0

    monkeypatch.setattr(
        "core_engine.services.rubika_circuit.get_circuit_snapshot", fake_circuit
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_inflight.get_campaign_safety_pause", fake_none
    )
    monkeypatch.setattr(
        "core_engine.services.rubika_preflight.evaluate_rubika_send_preflight", fake_pf
    )
    monkeypatch.setattr(
        "core_engine.services.rubika_health.build_health_snapshot", fake_health
    )
    monkeypatch.setattr(
        "core_engine.services.rubika_quota.read_quota_snapshot", fake_quota
    )
    monkeypatch.setattr("workers.pool_health.has_active_worker_coverage", fake_coverage)
    return PingRedis()


@pytest.fixture
def env(pg_session_factory):
    session = pg_session_factory()
    _rest_foreign_rubika(session)
    created_campaigns: list[int] = []
    yield session, created_campaigns
    session.rollback()
    if created_campaigns:
        session.query(StagedQueueItem).filter(
            StagedQueueItem.campaign_id.in_(created_campaigns)
        ).delete(synchronize_session=False)
        session.query(Message).filter(Message.campaign_id.in_(created_campaigns)).delete(
            synchronize_session=False
        )
        session.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id.in_(created_campaigns)
        ).delete(synchronize_session=False)
        session.query(CampaignAccount).filter(
            CampaignAccount.campaign_id.in_(created_campaigns)
        ).delete(synchronize_session=False)
        session.query(Campaign).filter(Campaign.id.in_(created_campaigns)).delete(
            synchronize_session=False
        )
    session.commit()
    session.close()


def test_1_and_2_automatic_draft_assigns_while_worker_off(env):
    session, campaigns = env
    account = _stable_account(session)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    before = _counts(session, campaign.id)
    result = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    after = _counts(session, campaign.id)
    assert result.created_assignments == 1
    assert result.eligible_accounts >= 1
    assert account.id in result.created_account_ids
    assert after["existing_accounts"] == before["existing_accounts"] + 1
    assert after["messages"] == 0
    assert after["queue"] == 0
    assert after["recipients"] == before["recipients"]
    session.refresh(campaign)
    assert campaign.status == CampaignStatus.DRAFT.value


@pytest.mark.asyncio
async def test_3_worker_off_still_blocks_start(env, monkeypatch):
    session, campaigns = env
    _stable_account(session)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    redis = _install_worker_off(monkeypatch)
    preflight = await evaluate_campaign_send_preflight(session, campaign.id, redis=redis)
    assert preflight.assigned_accounts >= 1
    assert preflight.worker_ready_accounts == 0
    assert preflight.execution_usable_accounts == 0
    assert preflight.allowed_to_start is False
    session.refresh(campaign)
    assert campaign.status == CampaignStatus.DRAFT.value


def test_4_rerun_is_idempotent(env):
    session, campaigns = env
    _stable_account(session)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    first = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    second = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    assert first.created_assignments == 1
    assert second.created_assignments == 0
    assert (
        session.query(func.count(CampaignAccount.id))
        .filter(CampaignAccount.campaign_id == campaign.id)
        .scalar()
        == 1
    )


def test_5_concurrent_inserts_do_not_duplicate(env, pg_session_factory):
    session, campaigns = env
    _stable_account(session)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    campaign_id = campaign.id

    def _run():
        other = pg_session_factory()
        try:
            row = other.query(Campaign).filter(Campaign.id == campaign_id).one()
            ensure_automatic_campaign_sender_assignments(other, row)
            other.commit()
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _i: _run(), range(2)))
    count = (
        session.query(func.count(CampaignAccount.id))
        .filter(CampaignAccount.campaign_id == campaign.id)
        .scalar()
    )
    assert count == 1


def test_6_manual_campaign_is_not_backfilled(env):
    session, campaigns = env
    eligible = _stable_account(session)
    extra = _stable_account(session)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    session.add(
        CampaignAccount(
            campaign_id=campaign.id,
            account_id=eligible.id,
            priority=1,
            enabled=True,
        )
    )
    session.commit()
    view = classify_campaign_for_automatic_backfill(session, campaign)
    assert view.eligible_for_backfill is False
    assert view.reason == "MANUAL_OR_EXISTING_ASSIGNMENT"
    results = backfill_automatic_rubika_campaign_senders(session, commit=True)
    matched = next(item for item in results if item.campaign_id == campaign.id)
    assert matched.created == 0
    ids = {
        account_id
        for (account_id,) in session.query(CampaignAccount.account_id)
        .filter(CampaignAccount.campaign_id == campaign.id)
        .all()
    }
    assert ids == {eligible.id}
    assert extra.id not in ids


def test_7_existing_manual_row_is_preserved_by_ensure(env):
    session, campaigns = env
    manual = _stable_account(session)
    other = _stable_account(session)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    session.add(
        CampaignAccount(
            campaign_id=campaign.id,
            account_id=manual.id,
            priority=1,
            enabled=True,
        )
    )
    session.commit()
    result = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    ids = {
        account_id
        for (account_id,) in session.query(CampaignAccount.account_id)
        .filter(CampaignAccount.campaign_id == campaign.id)
        .all()
    }
    assert manual.id in ids
    assert other.id in ids
    assert result.created_assignments == 1


def test_8_login_required_account_is_excluded(env):
    session, campaigns = env
    _stable_account(session, status=AccountStatus.REQUIRES_LOGIN)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    result = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    assert result.created_assignments == 0
    assert result.reason == "NO_STABLE_SENDERS"


def test_9_unconfirmed_activation_is_excluded(env):
    session, campaigns = env
    _stable_account(session, activation=RubikaAccountActivationState.PENDING)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    eligible, views = list_stable_assignment_eligible_accounts(session)
    assert eligible == []
    assert any(view.reason_excluded == "ACTIVATION_UNCONFIRMED" for view in views)
    result = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    assert result.created_assignments == 0


def test_10_archived_account_is_excluded(env):
    session, campaigns = env
    _stable_account(session, archived=True)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    result = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    assert result.created_assignments == 0


def test_11_pool_and_phase_exclusions(env):
    session, campaigns = env
    _stable_account(session, in_pool=False)
    _stable_account(session, phase="listener")
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    eligible, views = list_stable_assignment_eligible_accounts(session)
    assert eligible == []
    reasons = {view.reason_excluded for view in views if view.reason_excluded}
    assert "NOT_IN_POOL" in reasons
    assert "PHASE_NOT_ALLOWED" in reasons
    result = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    assert result.created_assignments == 0


def test_12_non_rubika_campaign_is_unchanged(env):
    session, campaigns = env
    _stable_account(session)
    campaign = _draft_campaign(session, platform=PlatformType.BALE)
    campaigns.append(campaign.id)
    result = ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    assert result.reason == "NOT_RUBIKA"
    assert result.created_assignments == 0
    assert _counts(session, campaign.id)["existing_accounts"] == 0


@pytest.mark.parametrize(
    "status,archived",
    [
        (CampaignStatus.RUNNING.value, False),
        (CampaignStatus.COMPLETED.value, False),
        (CampaignStatus.CANCELLED.value, False),
        (CampaignStatus.DRAFT.value, True),
    ],
)
def test_13_running_completed_cancelled_archived_are_skipped(env, status, archived):
    session, campaigns = env
    _stable_account(session)
    campaign = _draft_campaign(session, status=status, archived=archived)
    campaigns.append(campaign.id)
    view = classify_campaign_for_automatic_backfill(session, campaign)
    assert view.eligible_for_backfill is False
    results = backfill_automatic_rubika_campaign_senders(session, commit=True)
    matched = next(item for item in results if item.campaign_id == campaign.id)
    assert matched.created == 0
    assert _counts(session, campaign.id)["existing_accounts"] == 0


def test_14_campaign_with_message_or_queue_is_skipped(env):
    session, campaigns = env
    account = _stable_account(session)
    campaign = _draft_campaign(session, recipients=1)
    campaigns.append(campaign.id)
    recipient = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .one()
    )
    session.add(
        Message(
            campaign_id=campaign.id,
            account_id=account.id,
            contact_id=recipient.contact_id,
            dedupe_key=f"assign-{uuid.uuid4().hex}",
        )
    )
    session.commit()
    view = classify_campaign_for_automatic_backfill(session, campaign)
    assert view.eligible_for_backfill is False
    assert view.reason == "HAS_MESSAGES"

    queued = _draft_campaign(session, recipients=1)
    campaigns.append(queued.id)
    queued_recipient = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == queued.id)
        .one()
    )
    session.add(
        StagedQueueItem(
            campaign_id=queued.id,
            contact_id=queued_recipient.contact_id,
            channel="rubika",
            status=StagedQueueItemStatus.STAGED.value,
            final_text="x",
            queue_payload={"account_id": account.id},
        )
    )
    session.commit()
    queued_view = classify_campaign_for_automatic_backfill(session, queued)
    assert queued_view.eligible_for_backfill is False
    assert queued_view.reason == "HAS_QUEUE"


def test_15_from_import_assigns_future_campaign(env):
    session, campaigns = env
    account = _stable_account(session)
    batch = ImportBatch(
        file_name="assign.xlsx",
        original_file_name="assign.xlsx",
        status=ImportStatus.COMMITTED,
        row_count=1,
        valid_rows_count=1,
    )
    session.add(batch)
    session.flush()
    contact = Contact(
        first_name="import",
        phone=f"+989{abs(hash(uuid.uuid4().hex)) % 1_000_000_000:09d}",
        phone_e164=f"+989{uuid.uuid4().int % 10**10:010d}",
        consent_status="allowed",
        source_import_id=batch.id,
    )
    session.add(contact)
    session.commit()
    response = client.post(
        "/campaigns/from-import",
        json={
            "import_batch_id": batch.id,
            "title": f"from-import-{uuid.uuid4().hex[:8]}",
            "platform": "rubika",
            "template_text": "سلام",
            "account_ids": [],
            "use_gpt": False,
            "include_products": False,
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    campaigns.append(body["campaign_id"])
    assert account.id in body["account_ids"]
    assert body["sender_assignment"]["created_assignments"] >= 1
    counts = _counts(session, body["campaign_id"])
    assert counts["existing_accounts"] >= 1
    assert counts["messages"] == 0
    assert counts["queue"] == 0


def test_16_from_contacts_assigns_future_campaign(env):
    session, campaigns = env
    account = _stable_account(session)
    contact = Contact(
        first_name="contacts",
        phone=f"+989{abs(hash(uuid.uuid4().hex)) % 1_000_000_000:09d}",
        phone_e164=f"+989{uuid.uuid4().int % 10**10:010d}",
        consent_status="allowed",
    )
    session.add(contact)
    session.commit()
    response = client.post(
        "/campaigns/from-contacts",
        json={
            "contact_ids": [contact.id],
            "title": f"from-contacts-{uuid.uuid4().hex[:8]}",
            "platform": "rubika",
            "template_text": "سلام",
            "account_ids": [],
            "use_gpt": False,
            "include_products": False,
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    campaigns.append(body["campaign_id"])
    assert account.id in body["account_ids"]
    assert body["sender_assignment"]["created_assignments"] >= 1
    counts = _counts(session, body["campaign_id"])
    assert counts["existing_accounts"] >= 1
    assert counts["messages"] == 0
    assert counts["queue"] == 0


def test_17_assignment_never_creates_messages_or_queue(env):
    session, campaigns = env
    _stable_account(session)
    campaign = _draft_campaign(session)
    campaigns.append(campaign.id)
    ensure_automatic_campaign_sender_assignments(session, campaign)
    session.commit()
    backfill_automatic_rubika_campaign_senders(session, commit=True)
    counts = _counts(session, campaign.id)
    assert counts["messages"] == 0
    assert counts["queue"] == 0
    assert counts["existing_accounts"] >= 1
