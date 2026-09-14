"""L3 — Rubika login state machine (isolated DB, fake provider, no live OTP)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaIdentityStatus,
    RubikaLoginChallenge,
    RubikaLoginChallengeState,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.rubika_identity import bind_or_verify_rubika_identity
from core_engine.services.rubika_login_fake_provider import (
    FakeRubikaLoginProvider,
    PassThroughCandidateProver,
)
from core_engine.services.rubika_login_state_machine import (
    AUTH_RECONNECT_FAILED,
    IDENTITY_MISMATCH,
    LOGIN_ALREADY_COMPLETED,
    LOGIN_READY,
    OTP_ALREADY_PENDING,
    OTP_EXPIRED,
    OTP_INVALID,
    OTP_RATE_LIMITED,
    OTP_REQUESTED,
    CHALLENGE_WRONG_ACCOUNT,
    LEGACY_LOGIN_DISABLED,
    assert_legacy_login_allowed,
    request_rubika_login,
    submit_rubika_login_code,
)
from core_engine.services.rubika_user_session import RubikaLoginError, start_rubika_user_login
from core_engine.services.session_storage import store_channel_session
from core_engine.services.rubika_user_session import build_session_envelope


@pytest.fixture(autouse=True)
def _secrets_and_flag(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "true")
    monkeypatch.setenv("AUTO_ENROLL_RUBIKA_POOL", "false")
    monkeypatch.setenv("RUBIKA_OTP_RESEND_COOLDOWN_SECONDS", "60")
    monkeypatch.setenv("RUBIKA_OTP_CHALLENGE_TTL_SECONDS", "600")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _acct(db, phone="989120000111"):
    a = Account(
        platform=PlatformType.RUBIKA,
        phone_number=phone,
        label="l3",
        status=AccountStatus.ACTIVE,
        rubika_identity_status=RubikaIdentityStatus.UNBOUND,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _store_active(db, account_id, guid="guid-old"):
    env = build_session_envelope(
        phone_number="989120000111",
        auth="old-auth",
        guid=guid,
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nold\n-----END RSA PRIVATE KEY-----",
    )
    return store_channel_session(
        db,
        account_id=account_id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=env,
        session_status=RubikaSessionStatus.ACTIVE,
        identity_guid=guid,
    )


@pytest.mark.asyncio
async def test_first_otp_request_creates_one_challenge(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    assert r.ok and r.code == OTP_REQUESTED
    assert r.challenge_id
    n = db.query(RubikaLoginChallenge).filter_by(account_id=acct.id).count()
    assert n == 1
    ch = db.query(RubikaLoginChallenge).filter_by(id=r.challenge_id).one()
    assert ch.state == RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR


@pytest.mark.asyncio
async def test_repeated_request_otp_already_pending(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    r1 = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    r2 = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    assert not r2.ok and r2.code == OTP_ALREADY_PENDING
    assert r2.challenge_id == r1.challenge_id
    assert db.query(RubikaLoginChallenge).filter_by(account_id=acct.id).count() == 1


@pytest.mark.asyncio
async def test_resend_cooldown_enforced(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    now = datetime.now(timezone.utc)
    r1 = await request_rubika_login(
        db,
        acct.id,
        phone_number=acct.phone_number,
        provider=provider,
        secret_store=store,
        clock=now,
    )
    db.commit()
    # Expire active so cooldown path (not already-pending) is exercised.
    ch = db.query(RubikaLoginChallenge).filter_by(id=r1.challenge_id).one()
    ch.state = RubikaLoginChallengeState.LOGIN_FAILED
    db.commit()
    r2 = await request_rubika_login(
        db,
        acct.id,
        phone_number=acct.phone_number,
        provider=provider,
        secret_store=store,
        clock=now + timedelta(seconds=10),
    )
    assert not r2.ok and r2.code == OTP_RATE_LIMITED


@pytest.mark.asyncio
async def test_expired_challenge_rejected(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    now = datetime.now(timezone.utc)
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store, clock=now
    )
    db.commit()
    ch = db.query(RubikaLoginChallenge).filter_by(id=r.challenge_id).one()
    ch.expires_at = now - timedelta(seconds=1)
    db.commit()
    sub = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
        clock=now,
    )
    assert not sub.ok and sub.code == OTP_EXPIRED


@pytest.mark.asyncio
async def test_wrong_challenge_account_rejected(pg_session_factory):
    db = pg_session_factory()
    a1 = _acct(db, phone="989120000001")
    a2 = _acct(db, phone="989120000002")
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    r = await request_rubika_login(
        db, a1.id, phone_number=a1.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    sub = await submit_rubika_login_code(
        db,
        a2.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    assert not sub.ok and sub.code == CHALLENGE_WRONG_ACCOUNT


@pytest.mark.asyncio
async def test_otp_code_never_persisted(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider(expected_code="654321")
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "654321",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    # Scan challenge row columns / JSON dumps for the code.
    ch = db.query(RubikaLoginChallenge).filter_by(id=r.challenge_id).one()
    blob = f"{ch.id}{ch.failure_code}{ch.provider_challenge_id}{ch.phone_e164}"
    assert "654321" not in blob
    assert "654321" not in str(store.get(r.challenge_id, {}))


@pytest.mark.asyncio
async def test_success_creates_validating_then_active(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider(guid="guid-new")
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    sub = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    assert sub.ok and sub.code == LOGIN_READY
    assert sub.login_state == RubikaLoginChallengeState.READY.value
    actives = (
        db.query(ChannelSession)
        .filter_by(account_id=acct.id, session_status=RubikaSessionStatus.ACTIVE)
        .all()
    )
    assert len(actives) == 1


@pytest.mark.asyncio
async def test_reconnect_failure_not_ready(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    sub = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(force_fail_code=AUTH_RECONNECT_FAILED),
        secret_store=store,
    )
    db.commit()
    assert not sub.ok and sub.code == AUTH_RECONNECT_FAILED
    ch = db.query(RubikaLoginChallenge).filter_by(id=r.challenge_id).one()
    assert ch.state == RubikaLoginChallengeState.LOGIN_FAILED
    assert (
        db.query(ChannelSession)
        .filter_by(account_id=acct.id, session_status=RubikaSessionStatus.ACTIVE)
        .count()
        == 0
    )


@pytest.mark.asyncio
async def test_identity_mismatch_blocks_activation(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    bind_or_verify_rubika_identity(acct, "guid-bound")
    db.commit()
    store: dict = {}
    provider = FakeRubikaLoginProvider(guid="guid-other")
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    sub = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    assert not sub.ok and sub.code == IDENTITY_MISMATCH
    assert (
        db.query(ChannelSession)
        .filter_by(account_id=acct.id, session_status=RubikaSessionStatus.ACTIVE)
        .count()
        == 0
    )


@pytest.mark.asyncio
async def test_failed_candidate_preserves_old_active(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    bind_or_verify_rubika_identity(acct, "guid-old")
    old = _store_active(db, acct.id, guid="guid-old")
    db.commit()
    store: dict = {}
    provider = FakeRubikaLoginProvider(guid="guid-old")
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    sub = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(force_fail_code=AUTH_RECONNECT_FAILED),
        secret_store=store,
    )
    db.commit()
    assert not sub.ok
    db.refresh(old)
    assert old.session_status == RubikaSessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_successful_relogin_supersedes_old_active(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    bind_or_verify_rubika_identity(acct, "guid-same")
    old = _store_active(db, acct.id, guid="guid-same")
    db.commit()
    store: dict = {}
    provider = FakeRubikaLoginProvider(guid="guid-same")
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    sub = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    assert sub.ok
    db.refresh(old)
    assert old.session_status == RubikaSessionStatus.SUPERSEDED
    assert (
        db.query(ChannelSession)
        .filter_by(account_id=acct.id, session_status=RubikaSessionStatus.ACTIVE)
        .count()
        == 1
    )


@pytest.mark.asyncio
async def test_repeated_successful_submit_idempotent(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    s1 = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    s2 = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    assert s1.ok and s2.ok and s2.code == LOGIN_ALREADY_COMPLETED
    assert (
        db.query(ChannelSession)
        .filter_by(account_id=acct.id, session_status=RubikaSessionStatus.ACTIVE)
        .count()
        == 1
    )


@pytest.mark.asyncio
async def test_concurrent_submit_one_active(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()

    async def _one():
        # Separate session factory clone
        s = pg_session_factory()
        try:
            return await submit_rubika_login_code(
                s,
                acct.id,
                r.challenge_id,
                "123456",
                provider=provider,
                prover=PassThroughCandidateProver(),
                secret_store=store,
            )
        finally:
            s.commit()
            s.close()

    results = await asyncio.gather(_one(), _one(), return_exceptions=True)
    ok_ready = [
        x
        for x in results
        if not isinstance(x, Exception) and x.ok and x.code in {LOGIN_READY, LOGIN_ALREADY_COMPLETED, OTP_ALREADY_PENDING}
    ]
    assert len(ok_ready) >= 1
    actives = (
        db.query(ChannelSession)
        .filter_by(account_id=acct.id, session_status=RubikaSessionStatus.ACTIVE)
        .count()
    )
    assert actives <= 1


@pytest.mark.asyncio
async def test_login_ready_and_auth_dispatch_separation(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider(guid="guid-auth")
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    sub = await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    assert sub.login_state == "ready"
    assert sub.auth_ready is True
    assert sub.dispatch_ready is False
    assert sub.dispatch_block in {"NOT_IN_POOL", "NO_SCHEDULE", "NO_WORKER_CONSUMER"}


@pytest.mark.asyncio
async def test_no_auto_pool_enrollment(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    r = await request_rubika_login(
        db, acct.id, phone_number=acct.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    await submit_rubika_login_code(
        db,
        acct.id,
        r.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    from core_engine.models import RubikaAccountPool

    assert db.query(RubikaAccountPool).filter_by(account_id=acct.id).count() == 0


def test_legacy_path_fenced_when_flag_true():
    with pytest.raises(RubikaLoginError) as ei:
        assert_legacy_login_allowed()
    assert LEGACY_LOGIN_DISABLED.split(":")[0] in str(ei.value) or "LEGACY_LOGIN" in str(ei.value)


@pytest.mark.asyncio
async def test_legacy_start_raises_when_flag_true(pg_session_factory):
    with pytest.raises(RubikaLoginError):
        await start_rubika_user_login(account_id=1, phone_number="989120000001")


def test_legacy_allowed_when_flag_false(monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "false")
    get_settings.cache_clear()
    assert_legacy_login_allowed()  # must not raise


def test_login_module_does_not_restart_workers():
    import core_engine.services.rubika_login_state_machine as mod
    from pathlib import Path

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "docker" not in src.lower()
    assert "systemctl" not in src.lower()
    assert "restart_worker" not in src.lower()
