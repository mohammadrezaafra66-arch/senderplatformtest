"""R2.2 — Campaign vs Transport Rubika send-gate parity.

Proves that account-level rules shared by Campaign Preflight and Transport
Preflight emit the same reason code when evaluated with identical evidence.

Schedule isolation uses monkeypatch — never deactivates production
``RubikaSenderSchedule`` rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignStatus,
    PlatformType,
    RubikaAccountPool,
    SessionType,
)
from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
from core_engine.services.redis_client import get_redis_client, reset_redis_client
from core_engine.services.rubika_circuit import close_circuit, open_circuit
from core_engine.services.rubika_health import quarantine_account
from core_engine.services.rubika_policy import rubika_day_bucket
from core_engine.services.rubika_preflight import (
    ACCOUNT_BANNED,
    ACCOUNT_DISABLED,
    ACCOUNT_NOT_IN_ALLOWED_POOL,
    ACCOUNT_QUARANTINED,
    ACCOUNT_REQUIRES_LOGIN,
    COOLDOWN_ACTIVE,
    DAILY_CAP_REACHED,
    OUTSIDE_SEND_WINDOW,
    READY,
    RUBIKA_CIRCUIT_OPEN,
    SESSION_MISSING,
    evaluate_rubika_send_preflight,
)
from core_engine.services.rubika_quota import enter_cooldown
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.config import get_worker_settings
from workers.redis_keys import daily_rate_key, rubika_cooldown_meta_key, rubika_quarantine_key

IRAN = ZoneInfo("Asia/Tehran")
PARITY_PHASE = "r22-parity-day"


@pytest.fixture(autouse=True)
def _reset_redis():
    reset_redis_client()
    yield
    reset_redis_client()


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    monkeypatch.setenv("RUBIKA_DAILY_SEND_CAP", "5")
    monkeypatch.setenv("RUBIKA_HOURLY_SEND_CAP", "5")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


@pytest.fixture
def parity_phase(monkeypatch):
    """Pin current phase without touching production schedules."""

    def _phase(db, *, clock=None):
        if clock is not None:
            local = clock if clock.tzinfo is not None else clock.replace(tzinfo=IRAN)
            hour = local.astimezone(IRAN).hour
            # Outside-window tests use hour 23 with this fixture overridden.
            if hour >= 22 or hour < 8:
                return None
        return PARITY_PHASE

    monkeypatch.setattr(
        "workers.rubika_account_pool.resolve_current_phase",
        _phase,
    )
    # Campaign planner also uses loaded windows; keep capacity window open via clock.
    yield PARITY_PHASE


def _cleanup_pool(session, account_ids: list[int]):
    if not account_ids:
        return
    session.query(RubikaAccountPool).filter(
        RubikaAccountPool.account_id.in_(account_ids),
        RubikaAccountPool.phase == PARITY_PHASE,
    ).delete(synchronize_session=False)
    session.commit()


def _make_account(session, *, status=AccountStatus.ACTIVE, with_session: bool = True, in_pool: bool = True):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98913{abs(hash(uuid.uuid4().hex)) % 10_000_000:07d}",
        label=f"r22-{uuid.uuid4().hex[:6]}",
        status=status,
        warming_started_at=datetime.now(IRAN) - timedelta(days=30),
        last_used_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(account)
    session.flush()
    if with_session:
        envelope = build_session_envelope(
            phone_number=account.phone_number,
            auth="e" * 32,
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
    if in_pool:
        session.add(RubikaAccountPool(account_id=account.id, phase=PARITY_PHASE, priority=1))
    session.commit()
    session.refresh(account)
    return account


def _make_campaign(session, account: Account):
    campaign = Campaign(
        name=f"r22-{uuid.uuid4().hex[:8]}",
        title="r22",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.DRAFT.value,
        template_text="سلام",
    )
    session.add(campaign)
    session.flush()
    session.add(
        CampaignAccount(
            campaign_id=campaign.id,
            account_id=account.id,
            priority=1,
            enabled=True,
        )
    )
    session.commit()
    session.refresh(campaign)
    return campaign


async def _codes_for_account(session, campaign_id: int, account: Account, *, now: datetime):
    campaign_result = await evaluate_campaign_send_preflight(session, campaign_id, now=now)
    transport = await evaluate_rubika_send_preflight(
        session,
        account=account,
        campaign_id=campaign_id,
        context="parity_transport",
        clock=now,
        consume_circuit_probe=False,
    )
    campaign_account = next(
        (row for row in campaign_result.accounts if row.get("account_id") == account.id),
        None,
    )
    assert campaign_account is not None
    campaign_code = campaign_account.get("block_code") or READY
    return campaign_code, transport.code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        (AccountStatus.BANNED, ACCOUNT_BANNED),
        (AccountStatus.REQUIRES_LOGIN, ACCOUNT_REQUIRES_LOGIN),
        (AccountStatus.RESTING, ACCOUNT_DISABLED),
    ],
)
async def test_parity_account_status(pg_session_factory, parity_phase, status, expected):
    session = pg_session_factory()
    account = None
    try:
        account = _make_account(session, status=status)
        campaign = _make_campaign(session, account)
        now = datetime(2026, 8, 26, 12, 0, tzinfo=IRAN)
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == expected
        assert transport == expected
    finally:
        if account is not None:
            _cleanup_pool(session, [account.id])
        session.close()


@pytest.mark.asyncio
async def test_parity_session_missing(pg_session_factory, parity_phase):
    session = pg_session_factory()
    account = None
    try:
        account = _make_account(session, with_session=False)
        campaign = _make_campaign(session, account)
        now = datetime(2026, 8, 26, 12, 0, tzinfo=IRAN)
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == SESSION_MISSING
        assert transport == SESSION_MISSING
    finally:
        if account is not None:
            _cleanup_pool(session, [account.id])
        session.close()


@pytest.mark.asyncio
async def test_parity_outside_send_window(pg_session_factory, parity_phase, monkeypatch):
    session = pg_session_factory()
    account = None

    def _closed(db, *, clock=None):
        return None

    monkeypatch.setattr("workers.rubika_account_pool.resolve_current_phase", _closed)
    try:
        account = _make_account(session)
        campaign = _make_campaign(session, account)
        now = datetime(2026, 8, 26, 23, 0, tzinfo=IRAN)
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == OUTSIDE_SEND_WINDOW
        assert transport == OUTSIDE_SEND_WINDOW
    finally:
        if account is not None:
            _cleanup_pool(session, [account.id])
        session.close()


@pytest.mark.asyncio
async def test_parity_not_in_pool(pg_session_factory, parity_phase):
    session = pg_session_factory()
    account = None
    try:
        account = _make_account(session, in_pool=False)
        campaign = _make_campaign(session, account)
        now = datetime(2026, 8, 26, 12, 0, tzinfo=IRAN)
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == ACCOUNT_NOT_IN_ALLOWED_POOL
        assert transport == ACCOUNT_NOT_IN_ALLOWED_POOL
    finally:
        if account is not None:
            _cleanup_pool(session, [account.id])
        session.close()


@pytest.mark.asyncio
async def test_parity_quarantine(pg_session_factory, parity_phase):
    session = pg_session_factory()
    redis = get_redis_client()
    account = None
    try:
        account = _make_account(session)
        campaign = _make_campaign(session, account)
        await quarantine_account(
            redis,
            None,
            account.id,
            reason="r22-parity",
            source="test",
            ttl_seconds=600,
        )
        now = datetime(2026, 8, 26, 12, 0, tzinfo=IRAN)
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == ACCOUNT_QUARANTINED
        assert transport == ACCOUNT_QUARANTINED
    finally:
        if account is not None:
            await redis.delete(rubika_quarantine_key(account.id))
            _cleanup_pool(session, [account.id])
        session.close()


@pytest.mark.asyncio
async def test_parity_circuit_open(pg_session_factory, parity_phase):
    session = pg_session_factory()
    redis = get_redis_client()
    account = None
    try:
        account = _make_account(session)
        campaign = _make_campaign(session, account)
        await open_circuit(redis, reason="r22-parity", open_seconds=120)
        now = datetime(2026, 8, 26, 12, 0, tzinfo=IRAN)
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == RUBIKA_CIRCUIT_OPEN
        assert transport == RUBIKA_CIRCUIT_OPEN
    finally:
        await close_circuit(redis)
        if account is not None:
            _cleanup_pool(session, [account.id])
        session.close()


@pytest.mark.asyncio
async def test_parity_cooldown(pg_session_factory, parity_phase):
    session = pg_session_factory()
    redis = get_redis_client()
    account = None
    try:
        account = _make_account(session)
        campaign = _make_campaign(session, account)
        now = datetime(2026, 8, 26, 12, 0, tzinfo=IRAN)
        await enter_cooldown(
            redis,
            account.id,
            seconds=600,
            reason="r22",
            clock=now,
        )
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == COOLDOWN_ACTIVE
        assert transport == COOLDOWN_ACTIVE
    finally:
        if account is not None:
            await redis.delete(rubika_cooldown_meta_key(account.id))
            _cleanup_pool(session, [account.id])
        session.close()


@pytest.mark.asyncio
async def test_parity_daily_cap(pg_session_factory, parity_phase):
    session = pg_session_factory()
    redis = get_redis_client()
    account = None
    now = datetime(2026, 8, 26, 12, 0, tzinfo=IRAN)
    try:
        account = _make_account(session)
        campaign = _make_campaign(session, account)
        day = rubika_day_bucket(now)
        await redis.set(daily_rate_key(account.id, day), "5")
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == DAILY_CAP_REACHED
        assert transport == DAILY_CAP_REACHED
    finally:
        if account is not None:
            await redis.delete(daily_rate_key(account.id, rubika_day_bucket(now)))
            _cleanup_pool(session, [account.id])
        session.close()


@pytest.mark.asyncio
async def test_parity_ready(pg_session_factory, parity_phase):
    session = pg_session_factory()
    account = None
    try:
        account = _make_account(session)
        campaign = _make_campaign(session, account)
        now = datetime(2026, 8, 26, 12, 0, tzinfo=IRAN)
        camp, transport = await _codes_for_account(session, campaign.id, account, now=now)
        assert camp == READY
        assert transport == READY
    finally:
        if account is not None:
            _cleanup_pool(session, [account.id])
        session.close()
