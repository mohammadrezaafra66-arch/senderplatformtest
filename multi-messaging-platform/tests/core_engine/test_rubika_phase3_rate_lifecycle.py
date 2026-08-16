"""Rubika Phase 3 — rate limits, lifecycle, reservation, fail-closed proofs.

No real Rubika transport. Time and RNG are injected/frozen where needed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaAccountPool,
    RubikaSenderSchedule,
    SessionType,
)
from core_engine.services.rubika_mode import RUBIKA_MODE_USER_ACCOUNT
from core_engine.services.rubika_policy import (
    IRAN_TZ,
    RubikaLifecycleState,
    compute_jitter_seconds,
    compute_warmup_day,
    ensure_warming_started,
    resolve_effective_limits,
    resolve_rubika_lifecycle,
)
from core_engine.services.rubika_preflight import (
    ACCOUNT_THROTTLED,
    COOLDOWN_ACTIVE,
    DAILY_CAP_REACHED,
    HOURLY_CAP_REACHED,
    MIN_INTERVAL_ACTIVE,
    OUTSIDE_SEND_WINDOW,
    READY,
    REDIS_UNAVAILABLE,
    evaluate_rubika_send_preflight,
)
from core_engine.services.rubika_quota import (
    commit_reservation,
    enter_cooldown,
    enter_throttle,
    read_quota_snapshot,
    release_reservation,
    reserve_send_quota,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.config import WorkerSettings, get_worker_settings
from workers.connectors import rubika as rubika_connector
from workers.connectors.rubika import deliver_rubika_live
from workers.connectors.rubika_user import deliver_rubika_user_live
from workers.payloads import WorkerPayload
from workers.redis_keys import daily_rate_key, delay_key, hourly_rate_key
from workers.rubika_account_pool import resolve_current_phase

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


@pytest.fixture(autouse=True)
def _reset_redis():
    from core_engine.services.redis_client import get_redis_client, reset_redis_client

    reset_redis_client()
    yield
    try:
        redis = get_redis_client()
        # best-effort cleanup of rubika test keys is per-test
        _ = redis
    finally:
        reset_redis_client()


def _settings(**over) -> WorkerSettings:
    base = dict(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        RUBIKA_DELIVERY_MODE="user_account",
        RUBIKA_USER_ACCOUNT_ENABLED=True,
        RUBIKA_HOURLY_SEND_CAP=50,
        RUBIKA_DAILY_SEND_CAP=100,
        RUBIKA_MIN_SEND_DELAY_SECONDS=5,
        RUBIKA_MAX_SEND_DELAY_SECONDS=15,
        RUBIKA_JITTER_ENABLED=True,
        RUBIKA_RESERVE_TTL_SECONDS=60,
    )
    base.update(over)
    return WorkerSettings(**base)


def _payload(**over) -> WorkerPayload:
    base = dict(
        message_id=1,
        campaign_id="ops-p3",
        contact_id=0,
        account_id=1,
        platform="rubika",
        recipient="989120001234",
        recipient_type="phone",
        message_text="p3",
        dedupe_key="p3",
    )
    base.update(over)
    return WorkerPayload.model_validate(base)


def _make_account(
    session,
    *,
    label="p3",
    status=AccountStatus.ACTIVE,
    warming_days: int | None = 20,
):
    kwargs = dict(
        platform=PlatformType.RUBIKA,
        phone_number=f"98913{abs(hash(label)) % 10_000_000:07d}",
        label=label,
        status=status,
    )
    if warming_days is not None:
        kwargs["warming_started_at"] = datetime.now(timezone.utc) - timedelta(
            days=warming_days
        )
        kwargs["last_used_at"] = datetime.utcnow() - timedelta(days=1)
    account = Account(**kwargs)
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _store_user(session, account: Account):
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


def _full_window(session, phase="p3-day"):
    session.query(RubikaSenderSchedule).delete()
    session.add(
        RubikaSenderSchedule(
            phase=phase, start_hour=0, end_hour=24, max_per_hour=999, is_active=True
        )
    )
    session.commit()
    return phase


def _cleanup(session, ids: list[int]):
    session.query(ChannelSession).filter(ChannelSession.account_id.in_(ids)).delete(
        synchronize_session=False
    )
    session.query(RubikaAccountPool).filter(
        RubikaAccountPool.account_id.in_(ids)
    ).delete(synchronize_session=False)
    session.query(Account).filter(Account.id.in_(ids)).delete(synchronize_session=False)
    session.commit()


# --------------------------------------------------------------------------
# A. Lifecycle
# --------------------------------------------------------------------------


def test_lifecycle_mapping_and_restart_safe():
    clock = datetime(2026, 8, 16, 12, 0, tzinfo=IRAN)
    fresh = SimpleNamespace(
        status=AccountStatus.ACTIVE,
        warming_started_at=None,
        last_used_at=None,
    )
    assert resolve_rubika_lifecycle(fresh, clock=clock) == RubikaLifecycleState.NEW

    obs = SimpleNamespace(
        status=AccountStatus.ACTIVE,
        warming_started_at=clock.astimezone(timezone.utc) - timedelta(days=1),
        last_used_at=clock,
    )
    assert resolve_rubika_lifecycle(obs, clock=clock) == RubikaLifecycleState.OBSERVATION

    limited = SimpleNamespace(
        status=AccountStatus.ACTIVE,
        warming_started_at=clock.astimezone(timezone.utc) - timedelta(days=4),
        last_used_at=clock,
    )
    assert resolve_rubika_lifecycle(limited, clock=clock) == RubikaLifecycleState.LIMITED

    ramp = SimpleNamespace(
        status=AccountStatus.ACTIVE,
        warming_started_at=clock.astimezone(timezone.utc) - timedelta(days=10),
        last_used_at=clock,
    )
    assert resolve_rubika_lifecycle(ramp, clock=clock) == RubikaLifecycleState.RAMPING

    normal = SimpleNamespace(
        status=AccountStatus.ACTIVE,
        warming_started_at=clock.astimezone(timezone.utc) - timedelta(days=20),
        last_used_at=clock,
    )
    assert resolve_rubika_lifecycle(normal, clock=clock) == RubikaLifecycleState.NORMAL

    assert (
        resolve_rubika_lifecycle(normal, cooldown_active=True, clock=clock)
        == RubikaLifecycleState.COOLDOWN
    )
    assert (
        resolve_rubika_lifecycle(normal, throttle_active=True, clock=clock)
        == RubikaLifecycleState.THROTTLED
    )
    banned = SimpleNamespace(
        status=AccountStatus.BANNED, warming_started_at=None, last_used_at=None
    )
    assert resolve_rubika_lifecycle(banned, clock=clock) == RubikaLifecycleState.SUSPENDED

    # Restart-safe: same warming_started_at → same day/state after "restart"
    day1 = compute_warmup_day(normal.warming_started_at, clock=clock)
    day2 = compute_warmup_day(normal.warming_started_at, clock=clock + timedelta(hours=1))
    assert day1 == day2 == 20


def test_ensure_warming_started_persists_once():
    clock = datetime(2026, 8, 16, 12, 0, tzinfo=IRAN)
    account = SimpleNamespace(warming_started_at=None)
    assert ensure_warming_started(account, clock=clock) is True
    first = account.warming_started_at
    assert ensure_warming_started(account, clock=clock + timedelta(days=2)) is False
    assert account.warming_started_at == first


def test_stage_limits_are_conservative_and_configurable():
    limits_new = resolve_effective_limits(
        RubikaLifecycleState.NEW,
        configured_daily_cap=100,
        configured_hourly_cap=50,
        configured_min_interval=5,
        configured_max_interval=15,
    )
    assert limits_new.daily_cap == 3
    assert limits_new.hourly_cap == 1
    assert limits_new.min_interval_seconds == 90  # max(stage, cfg_min) → 90

    limits_normal = resolve_effective_limits(
        RubikaLifecycleState.NORMAL,
        configured_daily_cap=80,
        configured_hourly_cap=40,
        configured_min_interval=7,
        configured_max_interval=20,
        jitter_enabled=False,
    )
    assert limits_normal.daily_cap == 80
    assert limits_normal.hourly_cap == 40
    assert limits_normal.min_interval_seconds == 7


# --------------------------------------------------------------------------
# E. Jitter
# --------------------------------------------------------------------------


def test_jitter_bounded_deterministic_and_floor_safe():
    limits = resolve_effective_limits(
        RubikaLifecycleState.NORMAL,
        configured_daily_cap=100,
        configured_hourly_cap=50,
        configured_min_interval=10,
        configured_max_interval=20,
        jitter_enabled=True,
    )

    class Seq:
        def __init__(self, values):
            self.values = list(values)

        def randint(self, a, b):
            assert a == 10 and b == 20
            return self.values.pop(0)

    rng = Seq([10, 15, 20])
    assert compute_jitter_seconds(limits, rng=rng) == 10
    assert compute_jitter_seconds(limits, rng=rng) == 15
    assert compute_jitter_seconds(limits, rng=rng) == 20

    class BelowFloor:
        def randint(self, a, b):
            return 1  # malicious; still floored

    assert compute_jitter_seconds(limits, rng=BelowFloor()) == 10


# --------------------------------------------------------------------------
# B/C/D/F/G/H/I/J — preflight + quota
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_hourly_min_interval_and_policy_snapshot(
    monkeypatch, pg_session_factory
):
    from core_engine.services.redis_client import get_redis_client
    from core_engine.services.rubika_policy import rubika_day_bucket, rubika_hour_bucket

    session = pg_session_factory()
    account = _make_account(session, label="p3cap", warming_days=20)
    _store_user(session, account)
    phase = _full_window(session)
    session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
    session.commit()
    redis = get_redis_client()
    day = rubika_day_bucket()
    hour = rubika_hour_bucket()

    # under daily/hourly
    r = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
        daily_cap=10,
        hourly_cap=5,
    )
    assert r.code == READY
    assert r.details["policy"]["remaining_daily"] == 10
    assert r.details["policy"]["remaining_hourly"] == 5

    # daily boundary: count == cap → blocked
    await redis.set(daily_rate_key(account.id, day), "10", ex=3600)
    r_daily = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
        daily_cap=10,
        hourly_cap=50,
    )
    assert r_daily.code == DAILY_CAP_REACHED
    await redis.delete(daily_rate_key(account.id, day))

    # hourly boundary
    await redis.set(hourly_rate_key(account.id, hour), "5", ex=3600)
    r_hour = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
        daily_cap=100,
        hourly_cap=5,
    )
    assert r_hour.code == HOURLY_CAP_REACHED
    await redis.delete(hourly_rate_key(account.id, hour))

    # min interval
    await redis.set(delay_key(account.id), "1", ex=42)
    r_min = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
        daily_cap=100,
        hourly_cap=50,
    )
    assert r_min.code == MIN_INTERVAL_ACTIVE
    assert r_min.details["retry_after_seconds"] > 0
    assert "next_allowed_at" in r_min.details
    await redis.delete(delay_key(account.id))

    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_day_rollover_uses_iran_bucket(monkeypatch):
    from core_engine.services.rubika_policy import rubika_day_bucket

    before = datetime(2026, 8, 16, 23, 30, tzinfo=IRAN)
    after = datetime(2026, 8, 17, 0, 30, tzinfo=IRAN)
    assert rubika_day_bucket(before) == "20260816"
    assert rubika_day_bucket(after) == "20260817"
    assert rubika_day_bucket(before) != rubika_day_bucket(after)


@pytest.mark.asyncio
async def test_reservation_concurrency_and_release_commit():
    from core_engine.services.redis_client import get_redis_client
    from core_engine.services.rubika_policy import rubika_day_bucket, rubika_hour_bucket

    redis = get_redis_client()
    account_id = 910001
    day = rubika_day_bucket()
    hour = rubika_hour_bucket()
    await redis.delete(
        daily_rate_key(account_id, day),
        hourly_rate_key(account_id, hour),
        delay_key(account_id),
    )

    # Seed: remaining daily = 1
    await redis.set(daily_rate_key(account_id, day), "0", ex=3600)
    await redis.set(hourly_rate_key(account_id, hour), "0", ex=3600)

    results = await asyncio.gather(
        reserve_send_quota(redis, account_id, daily_cap=1, hourly_cap=10),
        reserve_send_quota(redis, account_id, daily_cap=1, hourly_cap=10),
    )
    ok = [r for r in results if r.ok]
    deny = [r for r in results if not r.ok]
    assert len(ok) == 1
    assert len(deny) == 1
    assert deny[0].code == DAILY_CAP_REACHED

    reservation = ok[0].reservation
    assert reservation is not None
    snap = await read_quota_snapshot(redis, account_id)
    assert snap.sent_today == 1

    # release restores capacity
    assert await release_reservation(redis, reservation) is True
    snap2 = await read_quota_snapshot(redis, account_id)
    assert snap2.sent_today == 0

    # commit keeps counters and sets delay
    r2 = await reserve_send_quota(redis, account_id, daily_cap=1, hourly_cap=10)
    assert r2.ok
    await commit_reservation(redis, r2.reservation, min_interval_seconds=30)
    assert await redis.ttl(delay_key(account_id)) > 0
    snap3 = await read_quota_snapshot(redis, account_id)
    assert snap3.sent_today == 1

    await redis.delete(
        daily_rate_key(account_id, day),
        hourly_rate_key(account_id, hour),
        delay_key(account_id),
    )


@pytest.mark.asyncio
async def test_cooldown_and_throttle_preflight(pg_session_factory):
    from core_engine.services.redis_client import get_redis_client

    session = pg_session_factory()
    account = _make_account(session, label="p3cd", warming_days=20)
    _store_user(session, account)
    phase = _full_window(session, "p3-cd")
    session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
    session.commit()
    redis = get_redis_client()

    until = await enter_cooldown(
        redis, account.id, seconds=120, reason="repeated_transport_failures"
    )
    r = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
    )
    assert r.code == COOLDOWN_ACTIVE
    assert r.details.get("cooldown_until") == until or r.details.get("reason")

    await redis.delete(delay_key(account.id))
    from workers.redis_keys import rubika_cooldown_meta_key

    await redis.delete(rubika_cooldown_meta_key(account.id))

    await enter_throttle(redis, account.id, seconds=90, reason="test")
    r2 = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
    )
    assert r2.code == ACCOUNT_THROTTLED
    from workers.redis_keys import rubika_throttle_key

    await redis.delete(rubika_throttle_key(account.id))
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_send_window_boundaries(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="p3win", warming_days=20)
    _store_user(session, account)
    session.query(RubikaSenderSchedule).delete()
    # Window 10:00–18:00 Iran; start inclusive, end exclusive (existing semantics).
    session.add(
        RubikaSenderSchedule(
            phase="day", start_hour=10, end_hour=18, max_per_hour=10, is_active=True
        )
    )
    session.commit()

    class FakeDT:
        @classmethod
        def now(cls, tz=None):
            return cls._now

    # just before opening
    FakeDT._now = datetime(2026, 8, 16, 9, 59, tzinfo=IRAN)
    monkeypatch.setattr("workers.rubika_account_pool.datetime", FakeDT)
    assert resolve_current_phase(session) is None

    # exact opening
    FakeDT._now = datetime(2026, 8, 16, 10, 0, tzinfo=IRAN)
    assert resolve_current_phase(session) == "day"

    # inside
    FakeDT._now = datetime(2026, 8, 16, 14, 0, tzinfo=IRAN)
    assert resolve_current_phase(session) == "day"

    # exact closing (exclusive)
    FakeDT._now = datetime(2026, 8, 16, 18, 0, tzinfo=IRAN)
    assert resolve_current_phase(session) is None

    # just after
    FakeDT._now = datetime(2026, 8, 16, 18, 1, tzinfo=IRAN)
    assert resolve_current_phase(session) is None

    # overnight wrap: 22→8
    session.query(RubikaSenderSchedule).delete()
    session.add(
        RubikaSenderSchedule(
            phase="night", start_hour=22, end_hour=8, max_per_hour=5, is_active=True
        )
    )
    session.commit()
    FakeDT._now = datetime(2026, 8, 16, 23, 0, tzinfo=IRAN)
    assert resolve_current_phase(session) == "night"
    FakeDT._now = datetime(2026, 8, 16, 7, 0, tzinfo=IRAN)
    assert resolve_current_phase(session) == "night"
    FakeDT._now = datetime(2026, 8, 16, 8, 0, tzinfo=IRAN)
    assert resolve_current_phase(session) is None

    # preflight outside window
    FakeDT._now = datetime(2026, 8, 16, 12, 0, tzinfo=IRAN)  # outside night
    from core_engine.services.redis_client import get_redis_client

    redis = get_redis_client()
    r = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=redis,
        check_runtime_limits=False,
        check_pool_membership=False,
        check_send_window=True,
    )
    assert r.code == OUTSIDE_SEND_WINDOW
    _cleanup(session, [account.id])
    session.close()


@pytest.mark.asyncio
async def test_redis_fail_closed_transport_not_called(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="p3redis", warming_days=20)
    _store_user(session, account)
    phase = _full_window(session, "p3-redis")
    session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
    session.commit()

    class Boom:
        async def ttl(self, *_a, **_k):
            raise TimeoutError("timeout")

        async def get(self, *_a, **_k):
            raise ConnectionError("down")

        async def exists(self, *_a, **_k):
            raise ConnectionError("down")

        async def eval(self, *_a, **_k):
            raise ConnectionError("down")

    transport = {"n": 0}

    async def boom_send(*_a, **_k):
        transport["n"] += 1
        raise AssertionError("transport")

    monkeypatch.setattr("rubpy.Client.send_message", boom_send)

    r = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
        redis=Boom(),
    )
    assert r.code == REDIS_UNAVAILABLE
    assert transport["n"] == 0

    # connector path
    from core_engine.services import redis_client as rc

    monkeypatch.setattr(rc, "get_redis_client", lambda: Boom())
    result = await deliver_rubika_user_live(
        _payload(account_id=account.id),
        _settings(),
        db=session,
    )
    assert result.success is False
    assert "redis" in (result.error_code or "")
    assert transport["n"] == 0
    _cleanup(session, [account.id])
    session.close()


# --------------------------------------------------------------------------
# L. Negative safety proofs — transport never called
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_policy_states_transport_not_called(
    monkeypatch, pg_session_factory
):
    from core_engine.services.redis_client import get_redis_client
    from core_engine.services.rubika_policy import rubika_day_bucket, rubika_hour_bucket
    from workers.redis_keys import rubika_throttle_key

    session = pg_session_factory()
    phase = _full_window(session, "p3-neg")
    transport = {"n": 0}

    async def fake_connect(self):
        return None

    async def fake_disconnect(self):
        return None

    async def fake_add(self, **kwargs):
        from rubpy.types import Update

        return Update({"user": {"user_guid": "uN", "phone": "989120000001"}, "user_exist": True})

    async def fake_send(self, **kwargs):
        transport["n"] += 1
        raise AssertionError("must not send")

    monkeypatch.setattr("rubpy.Client.connect", fake_connect)
    monkeypatch.setattr("rubpy.Client.disconnect", fake_disconnect)
    monkeypatch.setattr("rubpy.Client.add_address_book", fake_add)
    monkeypatch.setattr("rubpy.Client.send_message", fake_send)

    redis = get_redis_client()
    ids = []

    async def _blocked(label, setup_coro_factory):
        account = _make_account(session, label=label, warming_days=20)
        _store_user(session, account)
        session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
        session.commit()
        ids.append(account.id)
        await setup_coro_factory(account)
        before = transport["n"]
        result = await deliver_rubika_user_live(
            _payload(account_id=account.id, recipient="989120000001"),
            _settings(),
            db=session,
        )
        assert result.success is False
        assert transport["n"] == before

    day = rubika_day_bucket()
    hour = rubika_hour_bucket()

    async def seed_daily(a):
        await redis.set(daily_rate_key(a.id, day), "100", ex=3600)

    async def seed_hourly(a):
        await redis.set(hourly_rate_key(a.id, hour), "50", ex=3600)

    async def seed_delay(a):
        await redis.set(delay_key(a.id), "1", ex=60)

    async def seed_throttle(a):
        await redis.set(rubika_throttle_key(a.id), "x", ex=60)

    await _blocked("p3d", seed_daily)
    await _blocked("p3h", seed_hourly)
    await _blocked("p3m", seed_delay)
    await _blocked("p3t", seed_throttle)

    # suspended / banned
    banned = _make_account(
        session, label="p3ban", status=AccountStatus.BANNED, warming_days=20
    )
    _store_user(session, banned)
    ids.append(banned.id)
    session.commit()
    r_ban = await deliver_rubika_user_live(
        _payload(account_id=banned.id), _settings(), db=session
    )
    assert r_ban.success is False
    assert transport["n"] == 0

    # bot_api also blocked for banned
    calls = {"n": 0}

    async def boom_http(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("http")

    monkeypatch.setattr(rubika_connector, "request_rubika_api", boom_http)
    store_channel_session(
        session, account_id=banned.id, session_type=SessionType.API_TOKEN, plaintext="T"
    )
    session.commit()
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "bot_api")
    get_settings.cache_clear()
    r_bot = await deliver_rubika_live(
        _payload(account_id=banned.id, recipient="c1", recipient_type="channel_handle"),
        WorkerSettings(
            DRY_RUN=False,
            SHADOW_MODE=False,
            REAL_MESSAGE_SENDING_ENABLED=True,
            CHANNEL_CONNECTORS_ENABLED=True,
            RUBIKA_DELIVERY_MODE="bot_api",
            RUBIKA_API_BASE_URL="https://botapi.rubika.ir/v3",
            RUBIKA_API_TIMEOUT_SECONDS=5,
        ),
        db=session,
    )
    assert r_bot.success is False
    assert calls["n"] == 0

    for aid in ids:
        await redis.delete(
            daily_rate_key(aid, day),
            hourly_rate_key(aid, hour),
            delay_key(aid),
            rubika_throttle_key(aid),
        )
    _cleanup(session, ids)
    session.close()


@pytest.mark.asyncio
async def test_assigned_account_never_replaced(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    phase = _full_window(session, "p3-asg")
    assigned = _make_account(session, label="p3asg", warming_days=20)
    other = _make_account(session, label="p3oth", warming_days=20)
    _store_user(session, other)
    session.add(RubikaAccountPool(account_id=other.id, phase=phase, priority=1))
    session.commit()
    transport = {"n": 0}

    async def boom(*_a, **_k):
        transport["n"] += 1
        raise AssertionError("no")

    monkeypatch.setattr("rubpy.Client.send_message", boom)
    result = await deliver_rubika_user_live(
        _payload(account_id=assigned.id),
        _settings(),
        db=session,
    )
    assert result.success is False
    assert transport["n"] == 0
    _cleanup(session, [assigned.id, other.id])
    session.close()
