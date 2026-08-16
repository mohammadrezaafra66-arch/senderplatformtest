"""Rubika Phase 2 — preflight enforcement + transport-not-called proofs."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    ChannelSession,
    Contact,
    PlatformType,
    RubikaAccountPool,
    RubikaSenderSchedule,
    SessionType,
)
from core_engine.services.rubika_mode import RUBIKA_MODE_BOT_API, RUBIKA_MODE_USER_ACCOUNT
from core_engine.services.rubika_preflight import (
    ACCOUNT_BANNED,
    ACCOUNT_DISABLED,
    ACCOUNT_NOT_IN_ALLOWED_POOL,
    ACCOUNT_REQUIRES_LOGIN,
    CAMPAIGN_ACCOUNT_NOT_ALLOWED,
    COOLDOWN_ACTIVE,
    HOURLY_CAP_REACHED,
    MIN_INTERVAL_ACTIVE,
    OUTSIDE_SEND_WINDOW,
    READY,
    REDIS_UNAVAILABLE,
    SESSION_MISSING,
    USER_ACCOUNT_DISABLED,
    WRONG_PLATFORM,
    evaluate_rubika_send_preflight,
)
from core_engine.services.rubika_policy import rubika_hour_bucket
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.config import WorkerSettings
from workers.connectors import rubika as rubika_connector
from workers.connectors.rubika import deliver_rubika_live
from workers.connectors.rubika_user import deliver_rubika_user_live
from workers.payloads import WorkerPayload
from workers.redis_keys import delay_key, hourly_rate_key


@pytest.fixture(autouse=True)
def session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_redis():
    from core_engine.services.redis_client import reset_redis_client

    reset_redis_client()
    yield
    reset_redis_client()


def _pin(monkeypatch, mode: str, *, user_enabled: bool = False):
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", mode)
    monkeypatch.setenv(
        "RUBIKA_USER_ACCOUNT_ENABLED", "true" if user_enabled else "false"
    )
    get_settings.cache_clear()


def _bot_settings(**over) -> WorkerSettings:
    base = dict(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        RUBIKA_DELIVERY_MODE="bot_api",
        RUBIKA_USER_ACCOUNT_ENABLED=False,
        RUBIKA_API_BASE_URL="https://botapi.rubika.ir/v3",
        RUBIKA_API_TIMEOUT_SECONDS=5,
    )
    base.update(over)
    return WorkerSettings(**base)


def _user_settings(**over) -> WorkerSettings:
    base = dict(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        RUBIKA_DELIVERY_MODE="user_account",
        RUBIKA_USER_ACCOUNT_ENABLED=True,
        RUBIKA_HOURLY_SEND_CAP=50,
        RUBIKA_DAILY_SEND_CAP=100,
        RUBIKA_MIN_SEND_DELAY_SECONDS=1,
        RUBIKA_MAX_SEND_DELAY_SECONDS=2,
        RUBIKA_JITTER_ENABLED=False,
        RUBIKA_RESERVE_TTL_SECONDS=120,
    )
    base.update(over)
    return WorkerSettings(**base)


def _payload(**over) -> WorkerPayload:
    base = dict(
        message_id=1,
        campaign_id=10,
        contact_id=20,
        account_id=1,
        platform="rubika",
        recipient="chat-1",
        recipient_type="channel_handle",
        message_text="سلام",
        dedupe_key="p2-dedupe",
    )
    base.update(over)
    return WorkerPayload.model_validate(base)


def _make_account(session, *, status=AccountStatus.ACTIVE, platform=PlatformType.RUBIKA, label="p2"):
    account = Account(
        platform=platform,
        phone_number=f"98912{label[-6:].zfill(6)}" if platform == PlatformType.RUBIKA else "09120000000",
        label=label,
        status=status,
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _store_bot_token(session, account_id: int, token: str = "BOT_TOKEN"):
    store_channel_session(
        session,
        account_id=account_id,
        session_type=SessionType.API_TOKEN,
        plaintext=token,
    )
    session.commit()


def _store_user_envelope(session, account: Account):
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
    session.commit()


def _ensure_full_window(session, phase: str = "p2-day"):
    session.query(RubikaSenderSchedule).delete()
    session.add(
        RubikaSenderSchedule(
            phase=phase,
            start_hour=0,
            end_hour=24,
            max_per_hour=999,
            is_active=True,
        )
    )
    session.commit()
    return phase


def _cleanup(session, account_ids: list[int]):
    if not account_ids:
        return
    session.query(ChannelSession).filter(ChannelSession.account_id.in_(account_ids)).delete(
        synchronize_session=False
    )
    session.query(RubikaAccountPool).filter(
        RubikaAccountPool.account_id.in_(account_ids)
    ).delete(synchronize_session=False)
    session.query(CampaignAccount).filter(
        CampaignAccount.account_id.in_(account_ids)
    ).delete(synchronize_session=False)
    session.query(Account).filter(Account.id.in_(account_ids)).delete(
        synchronize_session=False
    )
    session.commit()


# --------------------------------------------------------------------------
# A. preflight unit
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_ready_bot_api(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_account(session, label="p2botok")
    _store_bot_token(session, account.id)
    result = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_BOT_API,
        check_runtime_limits=False,
        check_pool_membership=False,
        check_send_window=False,
    )
    assert result.allowed is True
    assert result.code == READY
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_preflight_banned_and_requires_login(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "bot_api")
    session = pg_session_factory()
    banned = _make_account(session, status=AccountStatus.BANNED, label="p2ban")
    _store_bot_token(session, banned.id)
    r1 = await evaluate_rubika_send_preflight(
        session, account=banned, delivery_mode=RUBIKA_MODE_BOT_API,
        check_runtime_limits=False, check_pool_membership=False, check_send_window=False,
    )
    assert r1.code == ACCOUNT_BANNED
    assert r1.retryable is False

    login = _make_account(session, status=AccountStatus.REQUIRES_LOGIN, label="p2login")
    _store_bot_token(session, login.id)
    r2 = await evaluate_rubika_send_preflight(
        session, account=login, delivery_mode=RUBIKA_MODE_BOT_API,
        check_runtime_limits=False, check_pool_membership=False, check_send_window=False,
    )
    assert r2.code == ACCOUNT_REQUIRES_LOGIN

    resting = _make_account(session, status=AccountStatus.RESTING, label="p2rest")
    _store_bot_token(session, resting.id)
    r3 = await evaluate_rubika_send_preflight(
        session, account=resting, delivery_mode=RUBIKA_MODE_BOT_API,
        check_runtime_limits=False, check_pool_membership=False, check_send_window=False,
    )
    assert r3.code == ACCOUNT_DISABLED
    _cleanup(session, [banned.id, login.id, resting.id])
    session.close()


@pytest.mark.asyncio
async def test_preflight_wrong_platform(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_account(session, platform=PlatformType.TELEGRAM, label="p2tg")
    r = await evaluate_rubika_send_preflight(session, account=account)
    assert r.code == WRONG_PLATFORM
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_preflight_user_account_disabled(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "user_account", user_enabled=False)
    session = pg_session_factory()
    account = _make_account(session, label="p2uadis")
    r = await evaluate_rubika_send_preflight(
        session, account=account, delivery_mode=RUBIKA_MODE_USER_ACCOUNT
    )
    assert r.code == USER_ACCOUNT_DISABLED
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_preflight_session_missing(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_account(session, label="p2miss")
    r = await evaluate_rubika_send_preflight(
        session, account=account, delivery_mode=RUBIKA_MODE_BOT_API,
        check_runtime_limits=False, check_pool_membership=False, check_send_window=False,
    )
    assert r.code == SESSION_MISSING
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_preflight_campaign_account_not_allowed(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "bot_api")
    session = pg_session_factory()
    allowed = _make_account(session, label="p2coka")
    other = _make_account(session, label="p2cokb")
    _store_bot_token(session, other.id)
    campaign = Campaign(
        name="p2-camp", title="p2-camp", channel="rubika", platform=PlatformType.RUBIKA
    )
    session.add(campaign)
    session.flush()
    session.add(
        CampaignAccount(campaign_id=campaign.id, account_id=allowed.id, enabled=True)
    )
    session.commit()

    r = await evaluate_rubika_send_preflight(
        session,
        account=other,
        delivery_mode=RUBIKA_MODE_BOT_API,
        campaign_id=campaign.id,
        check_runtime_limits=False,
        check_pool_membership=False,
        check_send_window=False,
    )
    assert r.code == CAMPAIGN_ACCOUNT_NOT_ALLOWED
    session.query(CampaignAccount).filter(CampaignAccount.campaign_id == campaign.id).delete()
    session.query(Campaign).filter(Campaign.id == campaign.id).delete()
    _cleanup(session, [allowed.id, other.id])
    session.close()


@pytest.mark.asyncio
async def test_preflight_outside_window_and_pool(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_account(session, label="p2win")
    _store_user_envelope(session, account)
    session.query(RubikaSenderSchedule).delete()
    session.commit()

    r = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        check_runtime_limits=False,
        check_pool_membership=True,
        check_send_window=True,
    )
    assert r.code == OUTSIDE_SEND_WINDOW

    phase = _ensure_full_window(session, "p2-pool")
    r2 = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        check_runtime_limits=False,
        check_pool_membership=True,
        check_send_window=True,
    )
    assert r2.code == ACCOUNT_NOT_IN_ALLOWED_POOL

    session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
    session.commit()
    r3 = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        check_runtime_limits=False,
        check_pool_membership=True,
        check_send_window=True,
    )
    assert r3.allowed is True
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_preflight_cooldown_and_hourly(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_account(session, label="p2cap")
    # Mature warming so NORMAL caps apply for hourly check.
    from datetime import datetime, timedelta, timezone

    account.warming_started_at = datetime.now(timezone.utc) - timedelta(days=20)
    _store_user_envelope(session, account)
    phase = _ensure_full_window(session, "p2-cap")
    session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
    session.commit()

    from core_engine.services.redis_client import get_redis_client

    redis = get_redis_client()
    await redis.set(delay_key(account.id), "1", ex=60)
    r = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
        hourly_cap=5,
    )
    assert r.code == MIN_INTERVAL_ACTIVE
    assert r.details.get("retry_after_seconds", 0) > 0
    await redis.delete(delay_key(account.id))

    hour = rubika_hour_bucket()
    await redis.set(hourly_rate_key(account.id, hour), "5", ex=3600)
    r2 = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
        hourly_cap=5,
    )
    assert r2.code == HOURLY_CAP_REACHED
    await redis.delete(hourly_rate_key(account.id, hour))
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_preflight_redis_fail_closed(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_account(session, label="p2redis")
    _store_user_envelope(session, account)
    phase = _ensure_full_window(session, "p2-redis")
    session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
    session.commit()

    class Boom:
        async def ttl(self, *_a, **_k):
            raise ConnectionError("down")

        async def get(self, *_a, **_k):
            raise ConnectionError("down")

        async def exists(self, *_a, **_k):
            raise ConnectionError("down")

        async def delete(self, *_a, **_k):
            raise ConnectionError("down")

        async def eval(self, *_a, **_k):
            raise ConnectionError("down")

        async def incr(self, *_a, **_k):
            raise ConnectionError("down")

        async def set(self, *_a, **_k):
            raise ConnectionError("down")

    r = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=Boom(),
        hourly_cap=50,
    )
    assert r.code == REDIS_UNAVAILABLE
    assert r.retryable is True
    _cleanup(session, [account.id])
    session.close()


# --------------------------------------------------------------------------
# B/C. connector enforcement + transport-not-called
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_api_blocked_states_transport_not_called(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "bot_api")
    session = pg_session_factory()
    calls = {"n": 0}

    async def boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("transport")

    monkeypatch.setattr(rubika_connector, "request_rubika_api", boom)
    monkeypatch.setattr(rubika_connector, "get_db_session", lambda: session)

    # missing session
    a1 = _make_account(session, label="p2b1")
    r1 = await deliver_rubika_live(_payload(account_id=a1.id), _bot_settings(), db=session)
    assert r1.success is False
    assert r1.error_code == "rubika_session_missing"

    # banned
    a2 = _make_account(session, status=AccountStatus.BANNED, label="p2b2")
    _store_bot_token(session, a2.id)
    r2 = await deliver_rubika_live(_payload(account_id=a2.id), _bot_settings(), db=session)
    assert r2.error_code == "rubika_account_banned"

    # requires login
    a3 = _make_account(session, status=AccountStatus.REQUIRES_LOGIN, label="p2b3")
    _store_bot_token(session, a3.id)
    r3 = await deliver_rubika_live(_payload(account_id=a3.id), _bot_settings(), db=session)
    assert r3.error_code == "rubika_account_requires_login"

    # wrong platform
    a4 = _make_account(session, platform=PlatformType.BALE, label="p2b4")
    r4 = await deliver_rubika_live(_payload(account_id=a4.id), _bot_settings(), db=session)
    assert r4.error_code == "rubika_wrong_platform"

    assert calls["n"] == 0
    _cleanup(session, [a1.id, a2.id, a3.id, a4.id])
    session.close()


@pytest.mark.asyncio
async def test_bot_api_valid_transport_once(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_account(session, label="p2bok")
    _store_bot_token(session, account.id, "LIVE_TOKEN")
    calls = {"n": 0}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"message_id": "99"}

    async def fake_request(*_a, **_k):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(rubika_connector, "request_rubika_api", fake_request)
    result = await deliver_rubika_live(
        _payload(account_id=account.id, recipient="chat-x"),
        _bot_settings(),
        db=session,
    )
    assert result.success is True
    assert calls["n"] == 1
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_user_account_blocked_and_valid(monkeypatch, pg_session_factory):
    _pin(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    phase = _ensure_full_window(session, "p2-u")
    transport = {"n": 0}

    async def fake_connect(self):
        return None

    async def fake_disconnect(self):
        return None

    async def fake_add_address_book(self, **kwargs):
        from rubpy.types import Update

        return Update(
            {
                "user": {"user_guid": "uX", "phone": "989120009001"},
                "user_exist": True,
            }
        )

    async def fake_send_message(self, **kwargs):
        transport["n"] += 1
        from rubpy.types import Update

        return Update({"message_id": "1"})

    monkeypatch.setattr("rubpy.Client.connect", fake_connect)
    monkeypatch.setattr("rubpy.Client.disconnect", fake_disconnect)
    monkeypatch.setattr("rubpy.Client.add_address_book", fake_add_address_book)
    monkeypatch.setattr("rubpy.Client.send_message", fake_send_message)

    # disabled config
    _pin(monkeypatch, "user_account", user_enabled=False)
    a0 = _make_account(session, label="p2u0")
    _store_user_envelope(session, a0)
    r0 = await deliver_rubika_user_live(
        _payload(account_id=a0.id, recipient="989120009001", recipient_type="phone"),
        _user_settings(RUBIKA_USER_ACCOUNT_ENABLED=False),
        db=session,
    )
    assert r0.error_code == "rubika_user_account_disabled"
    assert transport["n"] == 0

    _pin(monkeypatch, "user_account", user_enabled=True)
    # missing session
    a1 = _make_account(session, label="p2u1")
    session.add(RubikaAccountPool(account_id=a1.id, phase=phase, priority=1))
    session.commit()
    r1 = await deliver_rubika_user_live(
        _payload(account_id=a1.id, recipient="989120009001", recipient_type="phone"),
        _user_settings(),
        db=session,
    )
    assert r1.error_code == "rubika_session_missing"
    assert transport["n"] == 0

    # valid path
    a2 = _make_account(session, label="p2u2")
    from datetime import datetime, timedelta, timezone

    a2.warming_started_at = datetime.now(timezone.utc) - timedelta(days=20)
    _store_user_envelope(session, a2)
    session.add(RubikaAccountPool(account_id=a2.id, phase=phase, priority=1))
    phone_e164 = f"98912{a2.id:06d}"
    leftover = session.query(Contact).filter(Contact.phone_e164 == phone_e164).all()
    for row in leftover:
        session.delete(row)
    session.commit()
    contact = Contact(phone=phone_e164, phone_e164=phone_e164, first_name="t")
    session.add(contact)
    session.commit()

    r2 = await deliver_rubika_user_live(
        _payload(
            account_id=a2.id,
            contact_id=contact.id,
            recipient=phone_e164,
            recipient_type="phone",
            campaign_id="ops-test-1",
        ),
        _user_settings(),
        db=session,
    )
    assert r2.success is True
    assert transport["n"] == 1

    session.query(Contact).filter(Contact.id == contact.id).delete()
    _cleanup(session, [a0.id, a1.id, a2.id])
    session.close()


@pytest.mark.asyncio
async def test_user_account_respects_assigned_account_not_pool_replace(
    monkeypatch, pg_session_factory
):
    """Assigned payload.account_id must not be silently swapped for another pool account."""
    _pin(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    phase = _ensure_full_window(session, "p2-assign")
    assigned = _make_account(session, label="p2asg")
    other = _make_account(session, label="p2oth")
    _store_user_envelope(session, other)
    session.add(RubikaAccountPool(account_id=other.id, phase=phase, priority=1))
    # assigned is ACTIVE but NOT in pool and has no session
    session.commit()

    transport = {"n": 0}

    async def fake_send(*_a, **_k):
        transport["n"] += 1
        raise AssertionError("must not send")

    monkeypatch.setattr("rubpy.Client.send_message", fake_send)

    result = await deliver_rubika_user_live(
        _payload(
            account_id=assigned.id,
            recipient="989120009999",
            recipient_type="phone",
            campaign_id="ops-test-x",
        ),
        _user_settings(),
        db=session,
    )
    assert result.success is False
    assert result.error_code in {
        "rubika_session_missing",
        "rubika_account_not_in_allowed_pool",
    }
    assert transport["n"] == 0
    _cleanup(session, [assigned.id, other.id])
    session.close()
