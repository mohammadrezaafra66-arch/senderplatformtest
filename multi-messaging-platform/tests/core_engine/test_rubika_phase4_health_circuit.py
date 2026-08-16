"""Rubika Phase 4 — health, quarantine, circuit breaker, incidents.

No live Rubika transport. Time/RNG frozen or injected where needed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, PlatformType, SessionType
from core_engine.services.rubika_circuit import (
    RubikaCircuitState,
    assert_circuit_allows_send,
    close_circuit,
    enter_half_open,
    get_circuit_state,
    open_circuit,
    record_systemic_failure,
    try_acquire_probe,
)
from core_engine.services.rubika_failure import (
    RubikaFailureCategory,
    classify_rubika_failure,
)
from core_engine.services.rubika_health import (
    quarantine_account,
    record_rubika_send_failure,
    record_rubika_send_success,
    restore_rubika_account,
)
from core_engine.services.rubika_incidents import list_open_incidents, open_or_update_incident
from core_engine.services.rubika_mode import RUBIKA_MODE_BOT_API, RUBIKA_MODE_USER_ACCOUNT
from core_engine.services.rubika_preflight import (
    ACCOUNT_QUARANTINED,
    RUBIKA_CIRCUIT_OPEN,
    evaluate_rubika_send_preflight,
    require_rubika_side_channel_send,
)
from core_engine.services.session_storage import store_channel_session
from workers.config import WorkerSettings, get_worker_settings
from workers.connectors import rubika as rubika_connector
from workers.connectors.rubika import deliver_rubika_live
from workers.payloads import WorkerPayload
from workers.redis_keys import (
    rubika_circuit_probe_key,
    rubika_circuit_state_key,
    rubika_quarantine_key,
)


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "bot_api")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_redis():
    import subprocess

    from core_engine.services.redis_client import reset_redis_client

    def _flush() -> None:
        script = (
            "python3 - <<'PY'\n"
            "import redis\n"
            "r=redis.Redis.from_url('redis://localhost:6379/0')\n"
            "keys=list(r.scan_iter('rubika:*', count=200))\n"
            "if keys:\n"
            "    r.delete(*keys)\n"
            "r.delete('rubika:circuit:state','rubika:circuit:meta','rubika:circuit:probe')\n"
            "PY"
        )
        subprocess.run(["bash", "-lc", script], check=False, capture_output=True)

    reset_redis_client()
    _flush()
    yield
    _flush()
    reset_redis_client()


def _account(session, *, label="p4", status=AccountStatus.ACTIVE):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98914{abs(hash(label)) % 10_000_000:07d}",
        label=label,
        status=status,
        warming_started_at=datetime.now(timezone.utc) - timedelta(days=20),
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _bot_token(session, account_id: int, token: str = "P4TOKEN"):
    store_channel_session(
        session, account_id=account_id, session_type=SessionType.API_TOKEN, plaintext=token
    )
    session.commit()


def _payload(account_id: int) -> WorkerPayload:
    return WorkerPayload.model_validate(
        dict(
            message_id=1,
            campaign_id=10,
            contact_id=0,
            account_id=account_id,
            platform="rubika",
            recipient="chat-1",
            recipient_type="channel_handle",
            message_text="p4",
            dedupe_key="p4",
        )
    )


def _bot_settings(**over) -> WorkerSettings:
    base = dict(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        RUBIKA_DELIVERY_MODE="bot_api",
        RUBIKA_API_BASE_URL="https://botapi.rubika.ir/v3",
        RUBIKA_API_TIMEOUT_SECONDS=5,
        RUBIKA_CIRCUIT_PROBE_BUDGET=1,
        RUBIKA_CIRCUIT_DISTINCT_ACCOUNTS=3,
        RUBIKA_CIRCUIT_FAILURE_THRESHOLD=15,
        RUBIKA_HEALTH_WINDOW_SECONDS=3600,
    )
    base.update(over)
    return WorkerSettings(**base)


# --------------------------------------------------------------------------
# A. Failure classifier
# --------------------------------------------------------------------------


def test_failure_classifier_categories():
    cases = [
        ("rubika_unauthorized", RubikaFailureCategory.AUTH),
        ("SESSION_MISSING", RubikaFailureCategory.SESSION),
        ("rubika_user_rate_limited", RubikaFailureCategory.RATE_LIMIT),
        ("rubika_transport_error", RubikaFailureCategory.TRANSPORT),
        ("rubika_timeout", RubikaFailureCategory.TIMEOUT),
        ("rubika_chat_id_required", RubikaFailureCategory.RECIPIENT),
        ("CONFIG_INVALID", RubikaFailureCategory.CONFIG),
        ("REDIS_UNAVAILABLE", RubikaFailureCategory.DEPENDENCY_REDIS),
        ("DAILY_CAP_REACHED", RubikaFailureCategory.POLICY),
        ("totally_unknown_xyz", RubikaFailureCategory.UNKNOWN),
    ]
    for code, cat in cases:
        ev = classify_rubika_failure(code, retryable=True, account_id=1, source="test")
        assert ev.category == cat
        assert "token" not in ev.details


# --------------------------------------------------------------------------
# B/C. Health + quarantine
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_degraded_throttle_quarantine_and_block(
    monkeypatch, pg_session_factory
):
    from core_engine.services.redis_client import get_redis_client

    session = pg_session_factory()
    account = _account(session, label="p4h")
    _bot_token(session, account.id)
    redis = get_redis_client()

    # consecutive transport failures → escalate
    for i in range(5):
        await record_rubika_send_failure(
            redis,
            session,
            account_id=account.id,
            code="rubika_transport_error",
            retryable=True,
            consecutive_threshold=5,
            failure_count_threshold=100,
            failure_ratio_threshold=0.99,
            min_samples=50,
            cooldown_threshold=100,
            circuit_distinct_accounts=99,
            circuit_failure_threshold=999,
        )

    # quarantine explicitly and prove preflight/transport block
    await quarantine_account(
        redis,
        session,
        account.id,
        reason="test_quarantine",
        source="test",
        last_failure_code="rubika_transport_error",
        ttl_seconds=0,
    )
    session.commit()

    r = await evaluate_rubika_send_preflight(
        session,
        account=account,
        delivery_mode=RUBIKA_MODE_BOT_API,
        redis=redis,
        check_runtime_limits=False,
        check_pool_membership=False,
        check_send_window=False,
    )
    assert r.code == ACCOUNT_QUARANTINED
    assert r.details.get("reason") == "test_quarantine"

    transport = {"n": 0}

    async def boom(*_a, **_k):
        transport["n"] += 1
        raise AssertionError("transport")

    monkeypatch.setattr(rubika_connector, "request_rubika_api", boom)
    result = await deliver_rubika_live(
        _payload(account.id), _bot_settings(), db=session
    )
    assert result.success is False
    assert result.error_code == "rubika_account_quarantined"
    assert transport["n"] == 0

    # side channel also blocked
    side = await require_rubika_side_channel_send(
        session, account_id=account.id, context="ai", redis=redis
    )
    assert side.code == ACCOUNT_QUARANTINED
    session.close()


@pytest.mark.asyncio
async def test_restore_gates(pg_session_factory):
    from core_engine.services.redis_client import get_redis_client

    session = pg_session_factory()
    redis = get_redis_client()

    banned = _account(session, label="p4ban", status=AccountStatus.BANNED)
    r = await restore_rubika_account(
        session, redis, account_id=banned.id, username="admin", require_session_ready=False
    )
    assert r.ok is False
    assert r.code == "BANNED"

    other = Account(
        platform=PlatformType.BALE, phone_number="09120001111", label="p4bale", status=AccountStatus.ACTIVE
    )
    session.add(other)
    session.commit()
    session.refresh(other)
    r2 = await restore_rubika_account(
        session, redis, account_id=other.id, username="admin", require_session_ready=False
    )
    assert r2.code == "WRONG_PLATFORM"

    missing = await restore_rubika_account(
        session, redis, account_id=99999999, username="admin", require_session_ready=False
    )
    assert missing.code == "ACCOUNT_NOT_FOUND"

    ok_acct = _account(session, label="p4rest")
    await quarantine_account(
        redis, session, ok_acct.id, reason="tmp", source="test", ttl_seconds=0
    )
    # no session → SESSION_NOT_READY
    bad = await restore_rubika_account(
        session, redis, account_id=ok_acct.id, username="admin", require_session_ready=True
    )
    assert bad.code == "SESSION_NOT_READY"

    _bot_token(session, ok_acct.id)
    good = await restore_rubika_account(
        session, redis, account_id=ok_acct.id, username="admin", require_session_ready=True
    )
    assert good.ok is True
    assert good.code == "RESTORED"
    assert await redis.exists(rubika_quarantine_key(ok_acct.id)) == 0
    session.close()


@pytest.mark.asyncio
async def test_success_resets_consecutive(pg_session_factory):
    from core_engine.services.redis_client import get_redis_client
    from workers.redis_keys import rubika_health_consec_key

    session = pg_session_factory()
    account = _account(session, label="p4ok")
    redis = get_redis_client()
    await record_rubika_send_failure(
        redis,
        session,
        account_id=account.id,
        code="rubika_timeout",
        circuit_distinct_accounts=99,
        circuit_failure_threshold=999,
        cooldown_threshold=100,
    )
    assert int(await redis.get(rubika_health_consec_key(account.id)) or 0) == 1
    await record_rubika_send_success(redis, account.id)
    assert int(await redis.get(rubika_health_consec_key(account.id)) or 0) == 0
    session.close()


# --------------------------------------------------------------------------
# E. Circuit breaker
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_account_cannot_open_circuit(pg_session_factory):
    from core_engine.services.redis_client import get_redis_client

    redis = get_redis_client()
    await close_circuit(redis, reason="reset")
    for _ in range(20):
        opened = await record_systemic_failure(
            redis,
            account_id=42,
            distinct_accounts_threshold=3,
            failure_count_threshold=5,
            window_seconds=300,
        )
        assert opened is False
    assert await get_circuit_state(redis) == RubikaCircuitState.CLOSED


@pytest.mark.asyncio
async def test_multi_account_opens_and_blocks_transport(monkeypatch, pg_session_factory):
    from core_engine.services.redis_client import get_redis_client

    session = pg_session_factory()
    redis = get_redis_client()
    await close_circuit(redis, reason="reset")

    for aid in (101, 102, 103):
        await record_systemic_failure(
            redis,
            account_id=aid,
            distinct_accounts_threshold=3,
            failure_count_threshold=100,
            window_seconds=300,
            open_seconds=600,
        )
    assert await get_circuit_state(redis) == RubikaCircuitState.OPEN

    account = _account(session, label="p4cir")
    _bot_token(session, account.id)
    transport = {"n": 0}

    async def boom(*_a, **_k):
        transport["n"] += 1
        raise AssertionError("no")

    monkeypatch.setattr(rubika_connector, "request_rubika_api", boom)
    result = await deliver_rubika_live(_payload(account.id), _bot_settings(), db=session)
    assert result.success is False
    assert result.error_code == "rubika_circuit_open"
    assert result.retryable is True
    assert transport["n"] == 0

    side = await require_rubika_side_channel_send(
        session, account_id=account.id, context="status", redis=redis
    )
    assert side.code == RUBIKA_CIRCUIT_OPEN
    session.close()


@pytest.mark.asyncio
async def test_half_open_probe_budget_concurrency():
    from core_engine.services.redis_client import get_redis_client

    redis = get_redis_client()
    await open_circuit(redis, reason="test", open_seconds=1)
    # Force HALF_OPEN immediately
    await enter_half_open(redis, reason="test")

    results = await asyncio.gather(
        *[try_acquire_probe(redis, probe_budget=1) for _ in range(10)]
    )
    ok = [r for r in results if r[0]]
    deny = [r for r in results if not r[0]]
    assert len(ok) == 1
    assert len(deny) == 9


@pytest.mark.asyncio
async def test_half_open_success_closes_failure_reopens():
    from core_engine.services.redis_client import get_redis_client
    from core_engine.services.rubika_circuit import record_probe_failure, record_probe_success

    redis = get_redis_client()
    await enter_half_open(redis)
    state = await record_probe_success(redis, successes_to_close=1)
    assert state == RubikaCircuitState.CLOSED

    await enter_half_open(redis)
    await record_probe_failure(redis, open_seconds=300)
    assert await get_circuit_state(redis) == RubikaCircuitState.OPEN


@pytest.mark.asyncio
async def test_open_to_half_open_after_delay_injected_clock():
    from core_engine.services.redis_client import get_redis_client
    from core_engine.services.rubika_circuit import maybe_transition_open_to_half_open

    redis = get_redis_client()
    t0 = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    await open_circuit(redis, reason="t", open_seconds=60, clock=t0)
    still = await maybe_transition_open_to_half_open(
        redis, clock=t0 + timedelta(seconds=30)
    )
    assert still == RubikaCircuitState.OPEN
    nxt = await maybe_transition_open_to_half_open(
        redis, clock=t0 + timedelta(seconds=61)
    )
    assert nxt == RubikaCircuitState.HALF_OPEN


# --------------------------------------------------------------------------
# F. Incidents
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incident_dedup_and_scopes():
    from core_engine.services.redis_client import get_redis_client

    redis = get_redis_client()
    a = await open_or_update_incident(
        redis,
        scope="ACCOUNT",
        category="transport",
        severity="warning",
        reason="x",
        source="t",
        account_id=7,
        code="rubika_timeout",
    )
    b = await open_or_update_incident(
        redis,
        scope="ACCOUNT",
        category="transport",
        severity="warning",
        reason="x",
        source="t",
        account_id=7,
        code="rubika_timeout",
    )
    assert a.incident_id == b.incident_id
    assert b.occurrence_count == 2

    sys_i = await open_or_update_incident(
        redis,
        scope="SYSTEM",
        category="dependency_redis",
        severity="critical",
        reason="redis down",
        source="preflight",
        account_id=None,
        code="REDIS_UNAVAILABLE",
    )
    assert sys_i.scope == "SYSTEM"
    open_list = await list_open_incidents(redis)
    assert any(i.scope == "SYSTEM" for i in open_list)
    assert any(i.scope == "ACCOUNT" and i.account_id == 7 for i in open_list)


# --------------------------------------------------------------------------
# H. Dependency ≠ account quarantine
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_dependency_does_not_quarantine_account(pg_session_factory):
    from core_engine.services.redis_client import get_redis_client

    session = pg_session_factory()
    account = _account(session, label="p4dep")
    redis = get_redis_client()
    await record_rubika_send_failure(
        redis,
        session,
        account_id=account.id,
        code="REDIS_UNAVAILABLE",
        retryable=True,
    )
    assert await redis.exists(rubika_quarantine_key(account.id)) == 0
    session.close()


@pytest.mark.asyncio
async def test_assigned_account_not_replaced_when_quarantined(
    monkeypatch, pg_session_factory
):
    from core_engine.services.redis_client import get_redis_client
    from workers.connectors.rubika_user import deliver_rubika_user_live

    session = pg_session_factory()
    redis = get_redis_client()
    assigned = _account(session, label="p4asg")
    other = _account(session, label="p4oth")
    await quarantine_account(
        redis, session, assigned.id, reason="q", source="t", ttl_seconds=0
    )
    session.commit()
    transport = {"n": 0}

    async def boom(*_a, **_k):
        transport["n"] += 1
        raise AssertionError("no")

    monkeypatch.setattr("rubpy.Client.send_message", boom)
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    get_settings.cache_clear()
    result = await deliver_rubika_user_live(
        WorkerPayload.model_validate(
            dict(
                message_id=1,
                campaign_id="ops",
                contact_id=0,
                account_id=assigned.id,
                platform="rubika",
                recipient="989120000001",
                recipient_type="phone",
                message_text="x",
                dedupe_key="x",
            )
        ),
        WorkerSettings(
            DRY_RUN=False,
            SHADOW_MODE=False,
            REAL_MESSAGE_SENDING_ENABLED=True,
            CHANNEL_CONNECTORS_ENABLED=True,
            RUBIKA_DELIVERY_MODE="user_account",
            RUBIKA_USER_ACCOUNT_ENABLED=True,
        ),
        db=session,
    )
    assert result.success is False
    assert "quarantined" in (result.error_code or "")
    assert transport["n"] == 0
    # other account never used
    assert other.id != assigned.id
    session.close()
