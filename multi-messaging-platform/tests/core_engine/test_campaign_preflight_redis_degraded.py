"""Preflight must keep Postgres facts when Redis is down and never start a worker.

These tests do not publish coverage keys, do not start campaigns, and do not
create live queue jobs.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core_engine.config import get_settings
from core_engine.database import Base
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    PlatformType,
    RenderStatus,
    SendStatus,
    SessionType,
)
from core_engine.services.campaign_preflight import (
    CAMPAIGN_CAPACITY_UNKNOWN,
    CAMPAIGN_NO_SENDERS,
    NO_WORKER_CONSUMER,
    evaluate_campaign_send_preflight,
)
from core_engine.services.rubika_circuit import RubikaCircuitSnapshot
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from tests.isolation import assert_test_database_url, is_production_database_url
from workers.config import get_worker_settings

IRAN = ZoneInfo("Asia/Tehran")


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


@pytest.fixture
def pg_session_factory():
    url = os.environ.get("DATABASE_URL") or ""
    if is_production_database_url(url):
        pytest.fail("REFUSE: tests pointed at production database")
    assert_test_database_url(url)
    engine = create_engine(url)
    Base.metadata.create_all(engine, checkfirst=True)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield factory
    engine.dispose()


class BoomRedis:
    async def ping(self):
        raise RuntimeError("down")


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


def _closed_circuit() -> RubikaCircuitSnapshot:
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


def _install_redis_ok_worker_off(monkeypatch, *, coverage: bool = False):
    async def fake_circuit(*_a, **_k):
        return _closed_circuit()

    async def fake_pause(*_a, **_k):
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
        return coverage

    monkeypatch.setattr(
        "core_engine.services.rubika_circuit.get_circuit_snapshot", fake_circuit
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_inflight.get_campaign_safety_pause", fake_pause
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
    monkeypatch.setattr(
        "workers.pool_health.has_active_worker_coverage", fake_coverage
    )


def _make_account(session, *, label: str, with_session: bool = True) -> Account:
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98912{abs(hash(label + uuid.uuid4().hex)) % 10_000_000:07d}",
        label=label,
        status=AccountStatus.ACTIVE,
        warming_started_at=datetime.now(IRAN) - timedelta(days=20),
        last_used_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(account)
    session.flush()
    if with_session:
        envelope = build_session_envelope(
            phone_number=account.phone_number,
            auth="d" * 32,
            guid=f"u{account.id}",
            user_agent="ua",
            private_key="-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----",
        )
        store_channel_session(
            session,
            account_id=account.id,
            session_type=SessionType.RUBIKA_SESSION,
            plaintext=envelope,
        )
    session.commit()
    session.refresh(account)
    return account


def _draft_campaign(session, *, with_recipients: int = 3) -> Campaign:
    campaign = Campaign(
        name=f"pf-{uuid.uuid4().hex[:8]}",
        title="preflight-redis",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.DRAFT.value,
        template_text="سلام",
    )
    session.add(campaign)
    session.flush()
    for i in range(with_recipients):
        suffix = f"{abs(hash(uuid.uuid4().hex)) % 1_000_000_000:09d}"
        contact = Contact(
            first_name=f"r{i}",
            phone=f"+989{suffix}",
            phone_e164=f"+989{suffix}",
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


def _assign(session, campaign: Campaign, account: Account) -> None:
    session.add(
        CampaignAccount(
            campaign_id=campaign.id,
            account_id=account.id,
            priority=1,
            enabled=True,
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_redis_down_does_not_zero_campaign_accounts(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="kept")
    campaign = _draft_campaign(session, with_recipients=4)
    _assign(session, campaign, account)

    result = await evaluate_campaign_send_preflight(
        session, campaign.id, redis=BoomRedis()
    )
    assert result.allowed_to_start is False
    assert result.redis_ok is False
    assert result.redis_available is False
    assert result.capacity_known is False
    assert result.assigned_accounts == 1
    assert result.total_messages == 4
    assert any(row["account_id"] == account.id for row in result.accounts)
    assert result.immediate_capacity is None
    assert any(b["code"] == CAMPAIGN_CAPACITY_UNKNOWN for b in result.blockers)
    assert all(row.get("worker_coverage") is not True for row in result.accounts)
    assert all(row.get("eligible_now") is not True for row in result.accounts)
    session.close()


@pytest.mark.asyncio
async def test_redis_down_no_senders_keeps_no_senders_code(pg_session_factory):
    session = pg_session_factory()
    campaign = _draft_campaign(session, with_recipients=5)

    result = await evaluate_campaign_send_preflight(
        session, campaign.id, redis=BoomRedis()
    )
    assert result.allowed_to_start is False
    assert result.code == CAMPAIGN_NO_SENDERS
    assert result.assigned_accounts == 0
    assert result.total_messages == 5
    assert result.redis_ok is False
    assert any(b["code"] == CAMPAIGN_CAPACITY_UNKNOWN for b in result.blockers)
    assert any(b["code"] == CAMPAIGN_NO_SENDERS for b in result.blockers)
    session.close()


@pytest.mark.asyncio
async def test_redis_ok_no_senders(pg_session_factory, monkeypatch):
    _install_redis_ok_worker_off(monkeypatch, coverage=False)
    session = pg_session_factory()
    campaign = _draft_campaign(session, with_recipients=2)

    result = await evaluate_campaign_send_preflight(
        session, campaign.id, redis=PingRedis()
    )
    assert result.allowed_to_start is False
    assert result.code == CAMPAIGN_NO_SENDERS
    assert result.redis_ok is True
    assert result.assigned_accounts == 0
    assert not any(b["code"] == CAMPAIGN_CAPACITY_UNKNOWN for b in result.blockers)
    session.close()


@pytest.mark.asyncio
async def test_redis_ok_worker_off_authenticated_not_dispatch_ready(
    pg_session_factory, monkeypatch
):
    _install_redis_ok_worker_off(monkeypatch, coverage=False)
    session = pg_session_factory()
    account = _make_account(session, label="auth-no-worker")
    campaign = _draft_campaign(session, with_recipients=2)
    _assign(session, campaign, account)

    result = await evaluate_campaign_send_preflight(
        session, campaign.id, redis=PingRedis()
    )
    assert result.allowed_to_start is False
    assert result.redis_ok is True
    assert result.assigned_accounts == 1
    assert result.worker_ready_accounts == 0
    assert result.campaign_eligible_accounts == 0
    assert any(b["code"] == NO_WORKER_CONSUMER for b in result.blockers)
    assert not any(b["code"] == CAMPAIGN_CAPACITY_UNKNOWN for b in result.blockers)
    row = result.accounts[0]
    assert row["worker_coverage"] is False
    assert row["eligible_now"] is False
    assert row.get("runtime_status") != "LOGIN_REQUIRED"
    session.close()


@pytest.mark.asyncio
async def test_login_required_is_not_worker_gap(pg_session_factory, monkeypatch):
    _install_redis_ok_worker_off(monkeypatch, coverage=False)
    session = pg_session_factory()
    account = _make_account(session, label="needs-login", with_session=False)
    campaign = _draft_campaign(session, with_recipients=1)
    _assign(session, campaign, account)

    result = await evaluate_campaign_send_preflight(
        session, campaign.id, redis=PingRedis()
    )
    assert result.allowed_to_start is False
    row = result.accounts[0]
    assert row.get("runtime_status") in {"LOGIN_REQUIRED", "SESSION_MISSING", None} or (
        row.get("blocker_code") in {"LOGIN_REQUIRED", "SESSION_MISSING", "SESSION_INVALID"}
        or row.get("block_code") in {"LOGIN_REQUIRED", "SESSION_MISSING", "SESSION_INVALID"}
    )
    session.close()


@pytest.mark.asyncio
async def test_start_blocked_when_redis_unknown(pg_session_factory, monkeypatch):
    from core_engine.services.campaign_control import start_campaign
    from fastapi import HTTPException

    session = pg_session_factory()
    campaign = _draft_campaign(session, with_recipients=1)

    async def fake_eval(*_a, **_k):
        from core_engine.services.campaign_preflight import _unknown_result

        return _unknown_result(
            campaign.id,
            code=CAMPAIGN_CAPACITY_UNKNOWN,
            message="redis",
            evaluated_iso="2026-01-01T00:00:00+00:00",
            redis_ok=False,
            total_messages=1,
            assigned_accounts=0,
        )

    monkeypatch.setattr(
        "core_engine.services.campaign_control._auto_prepare",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_production_guards.controlled_production_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        fake_eval,
    )
    with pytest.raises(HTTPException) as exc:
        await start_campaign(session, campaign, trigger_bridge=False)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == CAMPAIGN_CAPACITY_UNKNOWN
    session.refresh(campaign)
    assert campaign.status == CampaignStatus.DRAFT.value
    session.close()


@pytest.mark.asyncio
async def test_start_blocked_when_worker_off(pg_session_factory, monkeypatch):
    from core_engine.services.campaign_control import start_campaign
    from fastapi import HTTPException
    from core_engine.services.campaign_preflight import CampaignPreflightResult, EXEC_BLOCKED

    session = pg_session_factory()
    campaign = _draft_campaign(session, with_recipients=1)

    async def fake_eval(*_a, **_k):
        return CampaignPreflightResult(
            allowed_to_start=False,
            code=NO_WORKER_CONSUMER,
            message="worker",
            campaign_id=campaign.id,
            execution_safety_state=EXEC_BLOCKED,
            total_messages=1,
            ready_messages=0,
            blocked_messages=0,
            assigned_accounts=1,
            usable_accounts=0,
            blocked_accounts=0,
            temporary_accounts=0,
            immediate_capacity=None,
            estimated_today_capacity=None,
            estimated_completion_at=None,
            estimated_duration_seconds=None,
            timezone="Asia/Tehran",
            redis_ok=True,
            redis_available=True,
            capacity_known=True,
            worker_ready_accounts=0,
        )

    monkeypatch.setattr(
        "core_engine.services.campaign_control._auto_prepare",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_production_guards.controlled_production_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        fake_eval,
    )
    with pytest.raises(HTTPException) as exc:
        await start_campaign(session, campaign, trigger_bridge=False)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == NO_WORKER_CONSUMER
    session.refresh(campaign)
    assert campaign.status == CampaignStatus.DRAFT.value
    session.close()
