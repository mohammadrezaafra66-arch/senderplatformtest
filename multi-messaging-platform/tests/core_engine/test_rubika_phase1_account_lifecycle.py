"""Phase 1 — Rubika account lifecycle, OTP, and campaign eligibility.

Isolated DB + fake login provider only. No live OTP, send, or worker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

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
from core_engine.services.account_runtime_status import (
    RuntimeStatus,
    compute_account_runtime_status,
)
from core_engine.services.campaign_sender_eligibility import evaluate_campaign_sender_eligibility
from core_engine.services.rubika_account_lifecycle import (
    RELOGIN_LABEL_FA,
    apply_rubika_session_invalidation,
    resolve_create_status,
)
from core_engine.services.rubika_login_fake_provider import (
    FakeRubikaLoginProvider,
    PassThroughCandidateProver,
)
from core_engine.services.rubika_login_state_machine import (
    OTP_EXPIRED,
    OTP_INVALID,
    OTP_RATE_LIMITED,
    OTP_REQUESTED,
    request_rubika_login,
    submit_rubika_login_code,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "true")
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    monkeypatch.setenv("AUTO_ENROLL_RUBIKA_POOL", "false")
    monkeypatch.setenv("RUBIKA_OTP_RESEND_COOLDOWN_SECONDS", "60")
    monkeypatch.setenv("RUBIKA_OTP_CHALLENGE_TTL_SECONDS", "600")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _acct(db, *, platform=PlatformType.RUBIKA, status=AccountStatus.REQUIRES_LOGIN, phone="989199910001"):
    account = Account(
        platform=platform,
        phone_number=phone,
        label="p1-life",
        status=status,
        rubika_identity_status=RubikaIdentityStatus.UNBOUND,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _existing_active_session(db, account, guid="guid-keep"):
    env = build_session_envelope(
        phone_number=account.phone_number,
        auth="keep-auth",
        guid=guid,
        user_agent="ua",
        private_key="pk-keep",
    )
    row = store_channel_session(
        db,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=env,
        session_status=RubikaSessionStatus.ACTIVE,
        identity_guid=guid,
    )
    account.rubika_guid = guid
    account.rubika_identity_status = RubikaIdentityStatus.VERIFIED
    db.commit()
    return row


async def _request(db, account, provider, store, *, clock=None):
    return await request_rubika_login(
        db,
        account.id,
        phone_number=account.phone_number,
        provider=provider,
        secret_store=store,
        clock=clock,
    )


def test_resolve_create_status_rubika_ignores_requested_active():
    assert (
        resolve_create_status(PlatformType.RUBIKA, AccountStatus.ACTIVE)
        == AccountStatus.REQUIRES_LOGIN
    )


def test_resolve_create_status_non_rubika_unchanged():
    assert resolve_create_status(PlatformType.BALE, AccountStatus.ACTIVE) == AccountStatus.ACTIVE
    assert resolve_create_status(PlatformType.TELEGRAM, AccountStatus.RESTING) == AccountStatus.RESTING
    assert resolve_create_status(PlatformType.WHATSAPP, AccountStatus.ACTIVE) == AccountStatus.ACTIVE


def test_new_rubika_without_session_not_campaign_eligible(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db)
    runtime = compute_account_runtime_status(db, account, worker_covered=True)
    elig = evaluate_campaign_sender_eligibility(db, account, runtime=runtime)
    assert account.status == AccountStatus.REQUIRES_LOGIN
    assert runtime.runtime_status == RuntimeStatus.LOGIN_REQUIRED.value
    assert elig.campaign_eligible is False
    assert runtime.runtime_status != RuntimeStatus.READY.value


@pytest.mark.asyncio
async def test_otp_request_sets_otp_waiting(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910002")
    store: dict = {}
    result = await _request(db, account, FakeRubikaLoginProvider(), store)
    db.commit()
    assert result.ok and result.code == OTP_REQUESTED
    runtime = compute_account_runtime_status(db, account, worker_covered=False)
    assert runtime.runtime_status == RuntimeStatus.OTP_WAITING.value
    assert account.status == AccountStatus.REQUIRES_LOGIN


@pytest.mark.asyncio
async def test_wrong_otp_does_not_activate_or_destroy_session(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910003")
    kept = _existing_active_session(db, account, guid="guid-wrong")
    store: dict = {}
    provider = FakeRubikaLoginProvider(expected_code="654321", guid="guid-wrong")
    requested = await _request(db, account, provider, store)
    db.commit()
    submitted = await submit_rubika_login_code(
        db,
        account.id,
        requested.challenge_id,
        "000000",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    db.refresh(account)
    db.refresh(kept)
    assert submitted.ok is False
    assert submitted.code == OTP_INVALID
    assert account.status == AccountStatus.REQUIRES_LOGIN
    assert kept.session_status == RubikaSessionStatus.ACTIVE
    assert (
        db.query(ChannelSession)
        .filter_by(account_id=account.id, session_status=RubikaSessionStatus.ACTIVE)
        .count()
        == 1
    )


@pytest.mark.asyncio
async def test_expired_otp_cannot_promote_and_needs_new_challenge(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910004")
    store: dict = {}
    now = datetime.now(timezone.utc)
    provider = FakeRubikaLoginProvider()
    requested = await _request(db, account, provider, store, clock=now)
    db.commit()
    challenge = db.query(RubikaLoginChallenge).filter_by(id=requested.challenge_id).one()
    challenge.expires_at = now - timedelta(seconds=1)
    db.commit()
    submitted = await submit_rubika_login_code(
        db,
        account.id,
        requested.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
        clock=now,
    )
    db.commit()
    db.refresh(account)
    assert submitted.ok is False
    assert submitted.code == OTP_EXPIRED
    assert account.status == AccountStatus.REQUIRES_LOGIN
    assert (
        db.query(ChannelSession)
        .filter_by(account_id=account.id, session_status=RubikaSessionStatus.ACTIVE)
        .count()
        == 0
    )
    challenge = db.query(RubikaLoginChallenge).filter_by(id=requested.challenge_id).one()
    assert challenge.state == RubikaLoginChallengeState.LOGIN_EXPIRED


@pytest.mark.asyncio
async def test_otp_resend_during_cooldown_blocked(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910005")
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    now = datetime.now(timezone.utc)
    first = await _request(db, account, provider, store, clock=now)
    db.commit()
    challenge = db.query(RubikaLoginChallenge).filter_by(id=first.challenge_id).one()
    challenge.state = RubikaLoginChallengeState.LOGIN_FAILED
    db.commit()
    second = await _request(
        db,
        account,
        provider,
        store,
        clock=now + timedelta(seconds=10),
    )
    assert second.ok is False
    assert second.code == OTP_RATE_LIMITED
    assert second.retry_after_seconds is not None
    assert second.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_otp_resend_after_cooldown_replaces_challenge(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910006")
    kept = _existing_active_session(db, account, guid="guid-resend")
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    now = datetime.now(timezone.utc)
    first = await _request(db, account, provider, store, clock=now)
    db.commit()
    second = await _request(
        db,
        account,
        provider,
        store,
        clock=now + timedelta(seconds=61),
    )
    db.commit()
    db.refresh(kept)
    assert second.ok is True
    assert second.code == OTP_REQUESTED
    assert second.challenge_id != first.challenge_id
    old = db.query(RubikaLoginChallenge).filter_by(id=first.challenge_id).one()
    assert old.state not in {
        RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR,
        RubikaLoginChallengeState.OTP_REQUESTED,
    }
    assert kept.session_status == RubikaSessionStatus.ACTIVE
    assert db.query(RubikaLoginChallenge).filter_by(account_id=account.id).count() == 2


@pytest.mark.asyncio
async def test_successful_otp_promotes_session_and_sets_active(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910007")
    store: dict = {}
    provider = FakeRubikaLoginProvider(guid="guid-ok")
    requested = await _request(db, account, provider, store)
    db.commit()
    submitted = await submit_rubika_login_code(
        db,
        account.id,
        requested.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    db.refresh(account)
    assert submitted.ok is True
    assert account.status == AccountStatus.ACTIVE
    actives = (
        db.query(ChannelSession)
        .filter_by(account_id=account.id, session_status=RubikaSessionStatus.ACTIVE)
        .all()
    )
    assert len(actives) == 1
    assert submitted.lifecycle_status == AccountStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_otp_success_without_worker_is_not_campaign_eligible(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910008")
    store: dict = {}
    provider = FakeRubikaLoginProvider(guid="guid-noworker")
    requested = await _request(db, account, provider, store)
    db.commit()
    await submit_rubika_login_code(
        db,
        account.id,
        requested.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    db.refresh(account)
    runtime = compute_account_runtime_status(
        db, account, worker_covered=False, dispatch_eligible_ids={account.id}
    )
    elig = evaluate_campaign_sender_eligibility(db, account, runtime=runtime)
    assert runtime.runtime_status == RuntimeStatus.AUTHENTICATED_NO_WORKER.value
    assert elig.campaign_eligible is False
    assert elig.worker_ready is False


@pytest.mark.asyncio
async def test_otp_success_with_worker_and_dispatch_is_ready(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910009")
    store: dict = {}
    provider = FakeRubikaLoginProvider(guid="guid-ready")
    requested = await _request(db, account, provider, store)
    db.commit()
    await submit_rubika_login_code(
        db,
        account.id,
        requested.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    db.refresh(account)
    runtime = compute_account_runtime_status(
        db, account, worker_covered=True, dispatch_eligible_ids={account.id}
    )
    elig = evaluate_campaign_sender_eligibility(db, account, runtime=runtime)
    assert runtime.runtime_status == RuntimeStatus.READY.value
    assert elig.campaign_eligible is True
    assert elig.worker_ready is True
    assert elig.dispatch_ready is True


@pytest.mark.asyncio
async def test_invalid_session_requires_login_and_blocks_eligibility(pg_session_factory):
    db = pg_session_factory()
    account = _acct(db, phone="989199910010", status=AccountStatus.ACTIVE)
    row = _existing_active_session(db, account, guid="guid-invalid")
    runtime_before = compute_account_runtime_status(
        db, account, worker_covered=True, dispatch_eligible_ids={account.id}
    )
    assert runtime_before.runtime_status == RuntimeStatus.READY.value

    apply_rubika_session_invalidation(db, account, reason="SessionInvalidError")
    db.commit()
    db.refresh(account)
    db.refresh(row)
    runtime = compute_account_runtime_status(
        db, account, worker_covered=True, dispatch_eligible_ids={account.id}
    )
    elig = evaluate_campaign_sender_eligibility(db, account, runtime=runtime)
    assert account.status == AccountStatus.REQUIRES_LOGIN
    assert row.session_status == RubikaSessionStatus.INVALID
    assert row.id is not None
    assert runtime.runtime_status != RuntimeStatus.READY.value
    assert elig.campaign_eligible is False
    assert runtime.runtime_status_label == RELOGIN_LABEL_FA


def test_non_rubika_create_status_unchanged_in_db(pg_session_factory):
    db = pg_session_factory()
    account = _acct(
        db,
        platform=PlatformType.BALE,
        status=resolve_create_status(PlatformType.BALE, AccountStatus.ACTIVE),
        phone="989199910011",
    )
    assert account.platform == PlatformType.BALE
    assert account.status == AccountStatus.ACTIVE
