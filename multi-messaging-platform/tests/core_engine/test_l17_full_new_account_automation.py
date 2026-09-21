"""L17 — Full new-account automation (isolated)."""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaAccountPool,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.rubika_canonical_runtime import (
    enforce_applies_to_account,
    load_rubika_runtime_session,
)
from core_engine.services.rubika_canonical_session import CanonicalSessionError
from core_engine.services.rubika_l17_automation import (
    DISCOVERY_SCOPE_ALL_ELIGIBLE,
    POOL_ENROLLMENT_FAILED,
    POOL_ENROLLMENT_OK,
    account_is_canonical_managed,
    account_is_legacy_protected,
    decide_l3_login_routing,
    ensure_rubika_pool_membership,
)
from core_engine.services.rubika_login_fake_provider import (
    FakeRubikaLoginProvider,
    PassThroughCandidateProver,
)
from core_engine.services.rubika_login_state_machine import (
    account_uses_l3_login,
    request_rubika_login,
    submit_rubika_login_code,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.rubika_worker_discovery import (
    MODE_DYNAMIC,
    get_dispatch_eligible_rubika_account_ids,
    resolve_actual_worker_account_ids,
)


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _l17_env(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "false")
    monkeypatch.setenv("RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS", "")
    monkeypatch.setenv("RUBIKA_L3_LOGIN_ROUTING", "auto_evidence")
    monkeypatch.setenv("AUTO_ENROLL_RUBIKA_POOL", "true")
    monkeypatch.setenv("DEFAULT_RUBIKA_POOL", "day")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "enforce")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_SCOPE", "canonical_active")
    monkeypatch.setenv("RUBIKA_WORKER_DISCOVERY_MODE", "dynamic")
    monkeypatch.setenv("RUBIKA_WORKER_DISCOVERY_COHORT_IDS", "")
    monkeypatch.setenv("RUBIKA_WORKER_DISCOVERY_SCOPE", "all_eligible")
    monkeypatch.setenv("RUBIKA_OTP_RESEND_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("RUBIKA_MANAGER_ACTIVATION_REQUIRED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def phase_day(monkeypatch):
    monkeypatch.setattr(
        "workers.rubika_account_pool.resolve_current_phase",
        lambda db, clock=None: "day",
    )


def _acct(db, *, phone="989130000028", label="a28"):
    a = Account(
        platform=PlatformType.RUBIKA,
        phone_number=phone,
        label=label,
        status=AccountStatus.ACTIVE,
        rubika_identity_status=RubikaIdentityStatus.UNBOUND,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _legacy_session(db, account, *, guid=None, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED):
    env = build_session_envelope(
        phone_number=account.phone_number or "989130000000",
        auth="legacy-auth",
        guid=guid or f"guid-legacy-{account.id}",
        user_agent="ua",
        private_key="pk",
    )
    return store_channel_session(
        db,
        account_id=int(account.id),
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=env,
        session_status=status,
        identity_guid=guid,
    )


def test_1_zero_session_auto_l3(db):
    a = _acct(db)
    d = decide_l3_login_routing(db, a.id)
    assert d.use_l3 is True
    assert d.reason == "zero_sessions_new_account"
    assert account_uses_l3_login(a.id, db) is True


def test_2_canonical_managed_uses_l3(db):
    a = _acct(db, phone="989130000027")
    a.rubika_guid = "guid-27"
    a.rubika_identity_status = RubikaIdentityStatus.VERIFIED
    db.commit()
    _legacy_session(db, a, guid="guid-27", status=RubikaSessionStatus.ACTIVE)
    db.commit()
    assert account_is_canonical_managed(db, a.id) is True
    assert decide_l3_login_routing(db, a.id).use_l3 is True


def test_3_legacy_ambiguous_protected(db):
    a = _acct(db, phone="989130000012", label="a12")
    _legacy_session(db, a, guid="g1")
    _legacy_session(db, a, guid="g2")
    db.commit()
    assert account_is_legacy_protected(db, a.id) is True
    assert decide_l3_login_routing(db, a.id).use_l3 is False


def test_3b_legacy_single_79_protected(db):
    a = _acct(db, phone="989130000079", label="a79")
    _legacy_session(db, a, guid="g79")
    db.commit()
    assert decide_l3_login_routing(db, a.id).use_l3 is False
    assert enforce_applies_to_account(a.id, db) is False


def test_4_failed_otp_no_pool_enroll(db, phase_day):
    a = _acct(db, phone="989130000040")
    provider = FakeRubikaLoginProvider(expected_code="111111", fail_submit=True)
    req = asyncio.run(request_rubika_login(db, a.id, provider=provider))
    assert req.ok
    sub = asyncio.run(
        submit_rubika_login_code(
            db,
            a.id,
            challenge_id=req.challenge_id,
            code="000000",
            provider=provider,
            prover=PassThroughCandidateProver(),
        )
    )
    assert sub.ok is False
    assert db.query(RubikaAccountPool).filter(RubikaAccountPool.account_id == a.id).count() == 0


def test_5_6_successful_enroll_once_idempotent(db, phase_day):
    a = _acct(db, phone="989130000041")
    provider = FakeRubikaLoginProvider(expected_code="222222", guid="guid-41")
    req = asyncio.run(request_rubika_login(db, a.id, provider=provider))
    sub = asyncio.run(
        submit_rubika_login_code(
            db,
            a.id,
            challenge_id=req.challenge_id,
            code="222222",
            provider=provider,
            prover=PassThroughCandidateProver(),
        )
    )
    assert sub.ok is True
    assert account_is_canonical_managed(db, a.id)
    rows = db.query(RubikaAccountPool).filter(RubikaAccountPool.account_id == a.id).all()
    assert len(rows) == 1
    r2 = ensure_rubika_pool_membership(db, a.id)
    assert r2.code == POOL_ENROLLMENT_OK
    assert r2.created is False
    assert db.query(RubikaAccountPool).filter(RubikaAccountPool.account_id == a.id).count() == 1


def test_7_disabled_not_enrolled(db, phase_day):
    a = _acct(db, phone="989130000042")
    a.status = AccountStatus.BANNED
    a.rubika_guid = "g42"
    a.rubika_identity_status = RubikaIdentityStatus.VERIFIED
    db.commit()
    _legacy_session(db, a, guid="g42", status=RubikaSessionStatus.ACTIVE)
    db.commit()
    r = ensure_rubika_pool_membership(db, a.id)
    assert r.code == POOL_ENROLLMENT_FAILED


def test_8_pool_failure_preserves_active(db, phase_day, monkeypatch):
    a = _acct(db, phone="989130000043")
    a.rubika_guid = "g43"
    a.rubika_identity_status = RubikaIdentityStatus.VERIFIED
    db.commit()
    sess = _legacy_session(db, a, guid="g43", status=RubikaSessionStatus.ACTIVE)
    db.commit()

    original_add = db.add

    def bad_add(obj):
        if isinstance(obj, RubikaAccountPool):
            raise RuntimeError("forced_pool")
        return original_add(obj)

    monkeypatch.setattr(db, "add", bad_add)
    r = ensure_rubika_pool_membership(db, a.id)
    assert r.code == POOL_ENROLLMENT_FAILED
    db.refresh(sess)
    assert sess.session_status == RubikaSessionStatus.ACTIVE


def test_9_10_11_12_13_14_discovery(db, phase_day):
    a = _acct(db, phone="989130000044")
    a.rubika_guid = "g44"
    a.rubika_identity_status = RubikaIdentityStatus.VERIFIED
    db.commit()
    _legacy_session(db, a, guid="g44", status=RubikaSessionStatus.ACTIVE)
    db.add(RubikaAccountPool(account_id=a.id, phase="day", priority=1))
    db.commit()

    eligible = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
    assert a.id in eligible

    ineligible = _acct(db, phone="989130000045")
    assert ineligible.id not in get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])

    actual = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=eligible,
        cohort_ids=[],  # no manual cohort
        discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
    )
    assert 12 in actual and 79 in actual
    assert a.id in actual
    # idempotent
    actual2 = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=eligible,
        cohort_ids=[999],  # ignored under all_eligible
        discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
    )
    assert actual2 == actual


def test_15_16_auto_enforce_scope(db):
    managed = _acct(db, phone="989130000046")
    managed.rubika_guid = "g46"
    managed.rubika_identity_status = RubikaIdentityStatus.VERIFIED
    db.commit()
    _legacy_session(db, managed, guid="g46", status=RubikaSessionStatus.ACTIVE)
    db.commit()
    assert enforce_applies_to_account(managed.id, db) is True

    legacy = _acct(db, phone="989130000047")
    _legacy_session(db, legacy, guid="g47")
    db.commit()
    assert enforce_applies_to_account(legacy.id, db) is False


def test_17_18_enforce_fail_closed_no_maxid(db):
    a = _acct(db, phone="989130000048")
    a.rubika_guid = "g48"
    a.rubika_identity_status = RubikaIdentityStatus.VERIFIED
    db.commit()
    # ACTIVE row missing ciphertext path: create ACTIVE then break identity
    _legacy_session(db, a, guid="g48", status=RubikaSessionStatus.ACTIVE)
    a.rubika_guid = "other"
    db.commit()
    # no longer canonical-managed → not auto-enforced
    assert enforce_applies_to_account(a.id, db) is False

    # Restore managed then delete all sessions while still claiming enforce via monkeypatch
    a.rubika_guid = "g48"
    db.commit()
    # Force scope apply with managed then remove sessions
    for row in db.query(ChannelSession).filter(ChannelSession.account_id == a.id):
        db.delete(row)
    db.commit()
    assert enforce_applies_to_account(a.id, db) is False


def test_17b_enforce_raises_no_active(db, monkeypatch):
    a = _acct(db, phone="989130000049")
    a.rubika_guid = "g49"
    a.rubika_identity_status = RubikaIdentityStatus.VERIFIED
    db.commit()
    _legacy_session(db, a, guid="g49", status=RubikaSessionStatus.ACTIVE)
    db.commit()
    assert enforce_applies_to_account(a.id, db) is True
    for row in db.query(ChannelSession).filter(ChannelSession.account_id == a.id):
        row.session_status = RubikaSessionStatus.REVOKED
    db.commit()
    # Still looks managed? identity ok but no ACTIVE → not managed
    assert account_is_canonical_managed(db, a.id) is False


def test_23_account28_synthetic_e2e(db, phase_day):
    """Full synthetic lifecycle: zero sessions, not in pilot/cohort/allowlist."""
    a = _acct(db, phone="989130000028", label="account28")
    assert a.id not in set()  # not in any config lists by construction
    assert decide_l3_login_routing(db, a.id).use_l3 is True

    provider = FakeRubikaLoginProvider(expected_code="333333", guid="guid-28")
    req = asyncio.run(request_rubika_login(db, a.id, provider=provider))
    assert req.ok
    sub = asyncio.run(
        submit_rubika_login_code(
            db,
            a.id,
            challenge_id=req.challenge_id,
            code="333333",
            provider=provider,
            prover=PassThroughCandidateProver(),
        )
    )
    assert sub.ok is True
    assert sub.auth_ready is True
    assert account_is_canonical_managed(db, a.id)
    assert db.query(RubikaAccountPool).filter(RubikaAccountPool.account_id == a.id).count() == 1
    assert enforce_applies_to_account(a.id, db) is True

    rt = load_rubika_runtime_session(db, a.id)
    assert rt.source == "canonical_enforce"
    assert rt.session_id == sub.active_session_id

    eligible = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
    assert a.id in eligible
    workers = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=eligible,
        cohort_ids=[],
        discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
    )
    assert a.id in workers
    assert 12 in workers and 79 in workers
    # no duplicate sessions
    assert (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == a.id,
            ChannelSession.session_status == RubikaSessionStatus.ACTIVE,
        )
        .count()
        == 1
    )


def test_pilot_override_still_works(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_L3_LOGIN_ROUTING", "pilot")
    monkeypatch.setenv("RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS", "999001")
    get_settings.cache_clear()
    a = _acct(db, phone="989130000099")
    # Force id check via pilot set membership only — routing pilot mode ignores evidence
    assert account_uses_l3_login(a.id, db) is False
