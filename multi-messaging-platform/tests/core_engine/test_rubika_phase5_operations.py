"""Rubika Phase 5 — Protection Center / alerts / overview API tests.

Disposable DB: prefer DATABASE_URL pointing at mmp_phase5_test.
No live Rubika transport. Isolated Redis keys under rubika:*.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountSendSettings,
    AccountStatus,
    AuditLog,
    ChannelSession,
    Message,
    MessageAttempt,
    PlatformType,
    SessionType,
)
from core_engine.services.redis_client import get_redis_client, reset_redis_client
from core_engine.services.rubika_alerts import (
    RubikaAlertType,
    list_open_alerts,
    redact_secrets,
    upsert_alert,
)
from core_engine.services.rubika_circuit import open_circuit
from core_engine.services.rubika_health import quarantine_account, restore_rubika_account
from core_engine.services.rubika_incidents import list_incidents, open_or_update_incident
from core_engine.services.rubika_operations import build_protection_overview
from core_engine.services.session_storage import store_channel_session
from workers.config import get_worker_settings

AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


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
    def _flush() -> None:
        import redis

        settings = get_settings()
        redis_url = settings.REDIS_URL

        _patterns: tuple[str, ...] = (
            "rubika:*",  # health/circuit/quarantine/incidents/alerts/etc
            "rate:*",  # rubika quota counters
            "config:delay:*",  # rubika min-interval delay
            "lock:rubika:*",  # best-effort: avoid stale quota/session locks
        )

        client = redis.Redis.from_url(redis_url, decode_responses=True)
        for pattern in _patterns:
            keys = list(client.scan_iter(match=pattern, count=200))
            if keys:
                client.delete(*keys)

        # Extra belt-and-suspenders: these keys are not only under rubika:*.
        for key in ("rubika:circuit:state", "rubika:circuit:meta", "rubika:circuit:probe"):
            client.delete(key)

    reset_redis_client()
    _flush()
    yield
    _flush()
    reset_redis_client()


@pytest.fixture(autouse=True)
def _cleanup_db(pg_session_factory):
    """Prevent Accounts created in these Redis incident/alerts tests from leaking."""

    yield

    session = pg_session_factory()
    try:
        account_ids = [
            row_id
            for (row_id,) in session.query(Account.id)
            .filter(
                Account.platform == PlatformType.RUBIKA,
                Account.phone_number.like("98915%"),
            )
            .all()
        ]
        if not account_ids:
            return

        # Best-effort cleanup of any message attempts created during protection restore flows.
        message_ids = [
            row_id
            for (row_id,) in session.query(Message.id)
            .filter(Message.account_id.in_(account_ids))
            .all()
        ]
        if message_ids:
            session.query(MessageAttempt).filter(
                MessageAttempt.message_id.in_(message_ids)
            ).delete(synchronize_session=False)
        session.query(Message).filter(Message.account_id.in_(account_ids)).delete(
            synchronize_session=False
        )

        session.query(ChannelSession).filter(
            ChannelSession.account_id.in_(account_ids)
        ).delete(synchronize_session=False)
        session.query(AccountSendSettings).filter(
            AccountSendSettings.account_id.in_(account_ids)
        ).delete(synchronize_session=False)
        session.query(AuditLog).filter(
            AuditLog.resource_type == "account",
            AuditLog.resource_id.in_([str(i) for i in account_ids]),
        ).delete(synchronize_session=False)
        session.query(Account).filter(Account.id.in_(account_ids)).delete(
            synchronize_session=False
        )
        session.commit()
    finally:
        session.close()


def _account(session, *, label="p5", status=AccountStatus.ACTIVE):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98915{abs(hash(label)) % 10_000_000:07d}",
        label=label,
        status=status,
        warming_started_at=datetime.now(timezone.utc) - timedelta(days=20),
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_alert_dedupe_and_redaction():
    redis = get_redis_client()
    a1 = await upsert_alert(
        redis,
        alert_type=RubikaAlertType.ACCOUNT_QUARANTINED.value,
        severity="CRITICAL",
        title="quarantine",
        message="ok",
        account_id=42,
        category="quarantine",
        metadata={"session_token": "SECRET123", "password": "x"},
    )
    assert a1.metadata.get("session_token") == "[REDACTED]"
    assert a1.metadata.get("password") == "[REDACTED]"
    a2 = await upsert_alert(
        redis,
        alert_type=RubikaAlertType.ACCOUNT_QUARANTINED.value,
        severity="CRITICAL",
        title="quarantine",
        message="ok again",
        account_id=42,
        category="quarantine",
    )
    assert a1.alert_id == a2.alert_id
    assert a2.occurrence_count == 2
    redacted = redact_secrets({"otp": "123456", "note": "fine"})
    assert redacted["otp"] == "[REDACTED]"
    assert redacted["note"] == "fine"
    open_items = await list_open_alerts(redis)
    assert any(i.dedupe_key == a1.dedupe_key for i in open_items)


@pytest.mark.asyncio
async def test_protection_overview_and_circuit(pg_session_factory):
    session = pg_session_factory()
    try:
        acct = _account(session, label="overview")
        redis = get_redis_client()
        await open_circuit(redis, reason="phase5_test", open_seconds=60)
        await open_or_update_incident(
            redis,
            scope="SYSTEM",
            category="circuit",
            severity="CRITICAL",
            reason="test",
            source="test",
            code="RUBIKA_CIRCUIT_OPEN",
        )
        overview = await build_protection_overview(session, redis)
        assert overview["system"]["circuit"]["state"] == "open"
        assert overview["summary"]["total_accounts"] >= 1
        assert any(a["account_id"] == acct.id for a in overview["accounts"])
        assert overview["summary"]["circuit_state"] == "open"
        assert any(a["type"] == "CIRCUIT_OPEN" for a in overview["alerts"])
        row = next(a for a in overview["accounts"] if a["account_id"] == acct.id)
        assert "health_state" in row
        assert "preflight" in row
        assert row["preflight"]["code"] == "RUBIKA_CIRCUIT_OPEN"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_incident_filters(pg_session_factory):
    redis = get_redis_client()
    await open_or_update_incident(
        redis,
        scope="ACCOUNT",
        category="rate_limit",
        severity="WARNING",
        reason="x",
        source="test",
        account_id=7,
        code="RATE",
    )
    await open_or_update_incident(
        redis,
        scope="SYSTEM",
        category="circuit",
        severity="CRITICAL",
        reason="y",
        source="test",
        code="CIRCUIT",
    )
    filtered = await list_incidents(redis, scope="ACCOUNT", severity="WARNING")
    assert len(filtered) == 1
    assert filtered[0].account_id == 7
    by_sev = await list_incidents(redis, sort="severity")
    assert by_sev[0].severity.upper() == "CRITICAL"


@pytest.mark.asyncio
async def test_restore_gates_banned_and_login(pg_session_factory):
    session = pg_session_factory()
    try:
        banned = _account(session, label="banned", status=AccountStatus.BANNED)
        login = _account(session, label="login", status=AccountStatus.REQUIRES_LOGIN)
        redis = get_redis_client()
        await quarantine_account(
            redis, session, banned.id, reason="test", source="test", ttl_seconds=0
        )
        await quarantine_account(
            redis, session, login.id, reason="test", source="test", ttl_seconds=0
        )
        session.commit()
        r1 = await restore_rubika_account(
            session, redis, account_id=banned.id, username="admin"
        )
        assert r1.ok is False
        assert r1.code == "BANNED"
        r2 = await restore_rubika_account(
            session, redis, account_id=login.id, username="admin"
        )
        assert r2.ok is False
        assert r2.code == "SESSION_NOT_READY"
        overview = await build_protection_overview(session, redis, include_alerts_sync=False)
        login_row = next(a for a in overview["accounts"] if a["account_id"] == login.id)
        assert login_row["restore"]["allowed"] is False
        assert login_row["restore"]["code"] == "REQUIRES_LOGIN"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_restore_success_audited(pg_session_factory):
    session = pg_session_factory()
    try:
        acct = _account(session, label="restore_ok", status=AccountStatus.ACTIVE)
        store_channel_session(
            session,
            account_id=acct.id,
            session_type=SessionType.API_TOKEN,
            plaintext="P5TOKEN",
        )
        session.commit()
        redis = get_redis_client()
        await quarantine_account(
            redis,
            session,
            acct.id,
            reason="sustained_failure_rate",
            source="test",
            ttl_seconds=0,
        )
        session.commit()
        result = await restore_rubika_account(
            session, redis, account_id=acct.id, username="admin"
        )
        assert result.ok is True
        audits = (
            session.query(AuditLog)
            .filter(AuditLog.action == "rubika_account_restore")
            .filter(AuditLog.resource_id == str(acct.id))
            .all()
        )
        assert len(audits) >= 1
    finally:
        session.close()
