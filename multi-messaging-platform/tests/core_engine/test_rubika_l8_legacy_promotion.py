"""L8 — Controlled legacy Rubika session promotion (isolated tests)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.rubika_candidate_prover import PROOF_PASS, CandidateProofResult
from core_engine.services.rubika_canonical_runtime import (
    clear_shadow_hooks,
    get_shadow_metrics,
    load_rubika_runtime_session,
    register_shadow_hook,
    SHADOW_MATCH,
)
from core_engine.services.rubika_canonical_session import load_canonical_rubika_session
from core_engine.services.rubika_legacy_promotion import (
    DECRYPT_FAILED,
    IDENTITY_CONFLICT,
    PROOF_REQUIRED,
    RECONNECT_FAILED,
    SESSION_WRONG_STATUS,
    LegacyPromotionEvidence,
    promote_proven_legacy_rubika_session,
    rollback_legacy_promotion,
    verify_post_promotion,
)
from core_engine.services.rubika_session_promotion import PROMOTION_OK
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import (
    encode_ciphertext_blob,
    store_channel_session,
)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "false")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "off")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "")
    get_settings.cache_clear()
    get_shadow_metrics().reset()
    clear_shadow_hooks()
    yield
    get_settings.cache_clear()
    get_shadow_metrics().reset()
    clear_shadow_hooks()


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
    finally:
        session.close()


def _envelope(guid: str, phone: str = "989120000013") -> str:
    return build_session_envelope(
        phone_number=phone,
        auth="auth-token-value",
        guid=guid,
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/fake\n-----END RSA PRIVATE KEY-----",
    )


def _acct(db, *, phone: str, guid: str | None = None, status=RubikaIdentityStatus.UNBOUND):
    a = Account(
        platform=PlatformType.RUBIKA,
        label=f"l8-{phone[-4:]}",
        phone_number=phone,
        status=AccountStatus.ACTIVE,
        rubika_identity_status=status,
        rubika_guid=guid,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _legacy_session(db, account: Account, *, guid: str) -> ChannelSession:
    row = store_channel_session(
        db,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=_envelope(guid, phone=account.phone_number or "989120000000"),
        session_status=RubikaSessionStatus.LEGACY_UNCLASSIFIED,
        identity_guid=None,
    )
    db.commit()
    db.refresh(row)
    return row


def _pass_proof(guid: str) -> CandidateProofResult:
    return CandidateProofResult(
        proof_status=PROOF_PASS,
        error_code=None,
        sanitized_message="ok",
        returned_identity_present=True,
        identity_match=True,
        duration_ms=1,
        identity_guid=guid,
    )


def _fail_proof(code: str) -> CandidateProofResult:
    return CandidateProofResult(
        proof_status=code,
        error_code=code,
        sanitized_message=code,
        returned_identity_present=False,
        identity_match=False,
        duration_ms=1,
        identity_guid=None,
    )


EVIDENCE = LegacyPromotionEvidence(source_phase="L8_TEST", operator_note="isolated")


@pytest.mark.asyncio
async def test_legacy_cannot_become_active_without_fresh_proof(db):
    acct = _acct(db, phone="989120000101")
    row = _legacy_session(db, acct, guid="guid-101")
    result = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-101",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=None,
        queue_length_fn=lambda _a: 0,
    )
    assert result.ok is False
    assert result.code == PROOF_REQUIRED
    db.refresh(row)
    assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED


@pytest.mark.asyncio
async def test_decrypt_failure_blocks_promotion(db):
    acct = _acct(db, phone="989120000102")
    row = _legacy_session(db, acct, guid="guid-102")
    # Corrupt ciphertext without going through encrypt helpers' valid path.
    row.ciphertext = encode_ciphertext_blob(b"not-a-fernet-token")
    db.commit()
    result = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-102",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-102"),
        queue_length_fn=lambda _a: 0,
    )
    assert result.ok is False
    assert result.code == DECRYPT_FAILED
    db.refresh(row)
    assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED


@pytest.mark.asyncio
async def test_reconnect_failure_blocks(db):
    acct = _acct(db, phone="989120000103")
    row = _legacy_session(db, acct, guid="guid-103")
    result = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-103",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_fail_proof(RECONNECT_FAILED),
        queue_length_fn=lambda _a: 0,
    )
    assert result.ok is False
    assert result.code in {RECONNECT_FAILED, PROOF_REQUIRED}
    db.refresh(row)
    assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED


@pytest.mark.asyncio
async def test_identity_mismatch_blocks_and_does_not_overwrite(db):
    acct = _acct(
        db,
        phone="989120000104",
        guid="existing-guid",
        status=RubikaIdentityStatus.VERIFIED,
    )
    row = _legacy_session(db, acct, guid="other-guid")
    result = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="other-guid",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("other-guid"),
        queue_length_fn=lambda _a: 0,
    )
    assert result.ok is False
    assert result.code == IDENTITY_CONFLICT
    db.refresh(acct)
    assert acct.rubika_guid == "existing-guid"
    db.refresh(row)
    assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED


@pytest.mark.asyncio
async def test_exact_candidate_promoted_and_one_active(db):
    acct = _acct(db, phone="989120000105")
    other = _legacy_session(db, acct, guid="guid-105")
    target = _legacy_session(db, acct, guid="guid-105")
    assert target.id != other.id
    result = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=target.id,
        expected_identity="guid-105",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-105"),
        queue_length_fn=lambda _a: 0,
    )
    assert result.ok is True
    assert result.code == PROMOTION_OK
    assert result.active_session_id == target.id
    db.refresh(target)
    db.refresh(other)
    assert target.session_status == RubikaSessionStatus.ACTIVE
    assert other.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED
    loaded = load_canonical_rubika_session(db, acct.id)
    assert loaded.session_id == target.id


@pytest.mark.asyncio
async def test_unrelated_sessions_unchanged(db):
    a1 = _acct(db, phone="989120000106")
    a2 = _acct(db, phone="989120000107")
    t1 = _legacy_session(db, a1, guid="guid-106")
    t2 = _legacy_session(db, a2, guid="guid-107")
    await promote_proven_legacy_rubika_session(
        db,
        account_id=a1.id,
        session_id=t1.id,
        expected_identity="guid-106",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-106"),
        queue_length_fn=lambda _a: 0,
    )
    db.refresh(t2)
    assert t2.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED


@pytest.mark.asyncio
async def test_new_guid_binding_only_after_proof(db):
    acct = _acct(db, phone="989120000108")
    assert acct.rubika_guid is None
    row = _legacy_session(db, acct, guid="guid-108")
    result = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-108",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-108"),
        queue_length_fn=lambda _a: 0,
    )
    assert result.ok
    assert result.identity_newly_bound is True
    db.refresh(acct)
    assert acct.rubika_guid == "guid-108"
    assert acct.rubika_identity_status == RubikaIdentityStatus.VERIFIED


@pytest.mark.asyncio
async def test_rollback_restores_exact_target_and_new_binding(db):
    acct = _acct(db, phone="989120000109")
    row = _legacy_session(db, acct, guid="guid-109")
    result = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-109",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-109"),
        queue_length_fn=lambda _a: 0,
    )
    assert result.ok
    rb = rollback_legacy_promotion(
        db,
        account_id=acct.id,
        session_id=row.id,
        identity_snapshot_before=result.identity_snapshot_before,
    )
    assert rb.ok
    assert rb.identity_restored is True
    db.refresh(row)
    db.refresh(acct)
    assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED
    assert acct.rubika_guid is None
    assert acct.rubika_identity_status == RubikaIdentityStatus.UNBOUND


@pytest.mark.asyncio
async def test_batch_promotion_is_account_by_account(db):
    accounts = []
    for i, phone in enumerate(("989120000110", "989120000111", "989120000112")):
        acct = _acct(db, phone=phone)
        row = _legacy_session(db, acct, guid=f"guid-11{i}")
        accounts.append((acct, row, f"guid-11{i}"))

    # Promote first only
    r0 = await promote_proven_legacy_rubika_session(
        db,
        account_id=accounts[0][0].id,
        session_id=accounts[0][1].id,
        expected_identity=accounts[0][2],
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof(accounts[0][2]),
        queue_length_fn=lambda _a: 0,
    )
    assert r0.ok
    for acct, row, _ in accounts[1:]:
        db.refresh(row)
        assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED

    # Second
    r1 = await promote_proven_legacy_rubika_session(
        db,
        account_id=accounts[1][0].id,
        session_id=accounts[1][1].id,
        expected_identity=accounts[1][2],
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof(accounts[1][2]),
        queue_length_fn=lambda _a: 0,
    )
    assert r1.ok
    db.refresh(accounts[2][1])
    assert accounts[2][1].session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED


@pytest.mark.asyncio
async def test_shadow_remains_non_authoritative_and_matches(db, monkeypatch):
    acct = _acct(db, phone="989120000113")
    row = _legacy_session(db, acct, guid="guid-113")
    await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-113",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-113"),
        queue_length_fn=lambda _a: 0,
    )
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "shadow")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(acct.id))
    get_settings.cache_clear()
    captured = []
    register_shadow_hook(lambda c: captured.append(c))
    selected = load_rubika_runtime_session(db, acct.id)
    assert selected.source == "legacy"
    assert selected.session_id == row.id
    assert captured and captured[0].metric == SHADOW_MATCH
    assert captured[0].match is True


def test_promotion_module_has_no_otp_or_send_path():
    import core_engine.services.rubika_legacy_promotion as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert "request_rubika_login" not in src
    assert "submit_rubika_login_code" not in src
    assert "send_message" not in src


@pytest.mark.asyncio
async def test_already_active_status_blocks_direct_reuse(db):
    """Cannot promote a row that is not LEGACY_UNCLASSIFIED."""
    acct = _acct(db, phone="989120000114", guid="guid-114", status=RubikaIdentityStatus.VERIFIED)
    row = store_channel_session(
        db,
        account_id=acct.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=_envelope("guid-114", phone=acct.phone_number or "98912"),
        session_status=RubikaSessionStatus.ACTIVE,
        identity_guid="guid-114",
    )
    db.commit()
    result = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-114",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-114"),
        queue_length_fn=lambda _a: 0,
    )
    assert result.ok is False
    assert result.code in {SESSION_WRONG_STATUS, "ACTIVE_ALREADY_EXISTS"}


@pytest.mark.asyncio
async def test_verify_post_promotion_match(db):
    acct = _acct(db, phone="989120000115")
    row = _legacy_session(db, acct, guid="guid-115")
    await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-115",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-115"),
        queue_length_fn=lambda _a: 0,
    )
    v = verify_post_promotion(db, account_id=acct.id, session_id=row.id)
    assert v["active_count"] == 1
    assert v["canonical_ok"] is True
    assert v["legacy_canonical_match"] is True


def test_classify_decrypt_failed_sibling_and_rollback(db):
    from core_engine.services.rubika_legacy_promotion import (
        classify_legacy_decrypt_failed_session,
        rollback_decrypt_failed_classification,
    )

    acct = _acct(db, phone="989120000216")
    bad = ChannelSession(
        account_id=acct.id,
        session_type=SessionType.RUBIKA_SESSION,
        ciphertext="not-a-valid-fernet-blob!!!",
        key_version=1,
        session_status=RubikaSessionStatus.LEGACY_UNCLASSIFIED,
    )
    db.add(bad)
    db.commit()
    db.refresh(bad)

    cls = classify_legacy_decrypt_failed_session(
        db,
        account_id=acct.id,
        session_id=bad.id,
        require_decrypt_failure=True,
    )
    assert cls["ok"] is True
    assert cls["post_status"] == RubikaSessionStatus.DECRYPT_FAILED.value
    db.commit()
    db.refresh(bad)
    assert bad.session_status == RubikaSessionStatus.DECRYPT_FAILED
    assert bad.validation_error_code == DECRYPT_FAILED

    rb = rollback_decrypt_failed_classification(
        db,
        account_id=acct.id,
        session_id=bad.id,
        prior_status="legacy_unclassified",
    )
    assert rb["ok"] is True
    db.commit()
    db.refresh(bad)
    assert bad.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED


def test_classify_refuses_active_row(db):
    from core_engine.services.rubika_legacy_promotion import (
        classify_legacy_decrypt_failed_session,
    )

    acct = _acct(db, phone="989120000217", guid="guid-217", status=RubikaIdentityStatus.VERIFIED)
    row = store_channel_session(
        db,
        account_id=acct.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=_envelope("guid-217", phone=acct.phone_number or "98912"),
        session_status=RubikaSessionStatus.ACTIVE,
        identity_guid="guid-217",
    )
    db.commit()
    cls = classify_legacy_decrypt_failed_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        require_decrypt_failure=False,
    )
    assert cls["ok"] is False
    assert cls["code"] == "REFUSE_CLASSIFY_ACTIVE"
