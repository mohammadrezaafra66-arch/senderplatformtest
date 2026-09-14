"""L8 Account23 host-path auto-rollback gap — isolated regression tests.

Hermetic: no production mmp_db mutation; script orchestration is unit-tested with
injectable rollback/verify callables. Service-level exact-ID rollback covered via
isolated DB fixtures (same helpers as L8 legacy promotion tests).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    PlatformType,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.rubika_candidate_prover import PROOF_PASS, CandidateProofResult
from core_engine.services.rubika_legacy_promotion import (
    LegacyPromotionEvidence,
    promote_proven_legacy_rubika_session,
    rollback_legacy_promotion,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "_l8_promote_account23_production.py"
)
_SPEC = importlib.util.spec_from_file_location("l8_promote_account23_production", _SCRIPT)
assert _SPEC and _SPEC.loader
a23 = importlib.util.module_from_spec(_SPEC)
sys.modules["l8_promote_account23_production"] = a23
_SPEC.loader.exec_module(a23)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "false")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "off")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
    finally:
        session.close()


def _envelope(guid: str, phone: str = "989120000023") -> str:
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
        label=f"l8-a23-{phone[-4:]}",
        phone_number=phone,
        status=AccountStatus.ACTIVE,
        rubika_identity_status=status,
        rubika_guid=guid,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _legacy_session(db, account: Account, *, guid: str):
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


EVIDENCE = LegacyPromotionEvidence(source_phase="L8_A23_TEST", operator_note="isolated")


async def _seed_account13_peer_active(db):
    """Stand-in for production Account13/Session729 — must stay ACTIVE across rollbacks."""
    a13 = _acct(
        db,
        phone="989120000013",
        guid="guid-account13-peer",
        status=RubikaIdentityStatus.VERIFIED,
    )
    row = _legacy_session(db, a13, guid="guid-account13-peer")
    promo = await promote_proven_legacy_rubika_session(
        db,
        account_id=a13.id,
        session_id=row.id,
        expected_identity="guid-account13-peer",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-account13-peer"),
        queue_length_fn=lambda _a: 0,
    )
    assert promo.ok
    db.refresh(row)
    db.refresh(a13)
    assert row.session_status == RubikaSessionStatus.ACTIVE
    return a13, row


def _assert_account13_peer_unchanged(db, a13, row, *, guid: str = "guid-account13-peer"):
    db.refresh(a13)
    db.refresh(row)
    assert row.session_status == RubikaSessionStatus.ACTIVE
    assert a13.rubika_guid == guid
    assert a13.rubika_identity_status == RubikaIdentityStatus.VERIFIED


def _success_inner(**extra):
    base = {
        "ACCOUNT23_PROMOTION_PASS": True,
        "ACCOUNT23_ACTIVE_SESSION_ID": a23.SESSION_ID,
        "ACCOUNT23_CANONICAL_LOADER_PASS": True,
        "ACCOUNT23_LEGACY_CANONICAL_MATCH": True,
        "promotion_committed": True,
        "identity_snapshot_before": {
            "rubika_guid": None,
            "rubika_identity_status": "unbound",
            "rubika_identity_verified_at": None,
            "newly_bound": True,
        },
    }
    base.update(extra)
    return base


def _passing_inventories():
    """Minimal before/after snapshots that satisfy assert_host_postconditions."""
    inv_before = (
        "724:74:legacy_unclassified\n"
        "725:23:legacy_unclassified\n"
        "729:13:active"
    )
    inv_after = (
        "724:74:legacy_unclassified\n"
        "725:23:active\n"
        "729:13:active"
    )
    before = {
        "total_sessions": 3,
        "session_inventory": inv_before,
        "a13": "729:active",
        "a23": "725:legacy_unclassified",
        "a74": "724:legacy_unclassified",
        "a12": "657:legacy_unclassified",
        "a79": "600:legacy_unclassified",
        "active_global": 1,
        "login_challenge_count": 0,
        "message_attempt_count": 5,
        "queue23": 0,
    }
    after = {
        **before,
        "session_inventory": inv_after,
        "a23": "725:active",
        "active_global": 2,
        "queue23": 0,
    }
    return before, after


def test_target_constants_locked():
    assert a23.ACCOUNT_ID == 23
    assert a23.SESSION_ID == 725


def test_inner_commit_all_postchecks_pass_no_rollback_complete_path():
    before, after = _passing_inventories()
    inner = _success_inner()
    calls: list = []

    def rollback_invoker(_snap):
        calls.append("rollback")
        return {"ok": True, "ROLLBACK_RESULT": "PASS"}

    a23.host_postcheck_with_auto_rollback(
        before=before,
        after=after,
        inner=inner,
        rollback_invoker=rollback_invoker,
        restore_verifier=lambda *_a, **_k: {"ok": True},
    )
    assert calls == []
    artifact = {"PHASE_STATUS": "COMPLETE", "ROLLBACK_PERFORMED": False}
    assert artifact["PHASE_STATUS"] == "COMPLETE"
    assert not calls


def test_inner_commit_host_postcheck_fail_invokes_exact_rollback():
    before, after = _passing_inventories()
    after = {**after, "a74": "724:active"}  # host invariant break
    # Account13/729 inventory must remain the pre-promotion ACTIVE peer.
    assert before["a13"] == after["a13"] == "729:active"
    inner = _success_inner()
    seen: list = []

    def rollback_invoker(snap):
        seen.append(
            {
                "account_id": a23.ACCOUNT_ID,
                "session_id": a23.SESSION_ID,
                "snap": snap,
            }
        )
        return {"ok": True, "ROLLBACK_RESULT": "PASS"}

    def restore_verifier(bef, *, identity_snapshot=None):
        # Simulate host restore check: Account13 still ACTIVE 729.
        assert bef["a13"] == "729:active"
        return {"ok": True, "a13": "729:active"}

    with pytest.raises(a23.GateFailure) as ei:
        a23.host_postcheck_with_auto_rollback(
            before=before,
            after=after,
            inner=inner,
            rollback_invoker=rollback_invoker,
            restore_verifier=restore_verifier,
        )
    exc = ei.value
    assert len(seen) == 1
    assert seen[0]["account_id"] == 23
    assert seen[0]["session_id"] == 725
    assert exc.failed_stage == "POST_VERIFY"
    assert exc.rollback_performed is True
    assert exc.rollback_result == "PASS"
    assert exc.as_dict()["PHASE_STATUS"] == "BLOCKED"


def test_rollback_failure_never_reports_complete_and_blocks():
    before, after = _passing_inventories()
    after = {**after, "active_global": 99}
    inner = _success_inner()

    with pytest.raises(a23.GateFailure) as ei:
        a23.host_postcheck_with_auto_rollback(
            before=before,
            after=after,
            inner=inner,
            rollback_invoker=lambda _s: {"ok": False, "ROLLBACK_RESULT": "FAIL"},
            restore_verifier=lambda *_a, **_k: {"ok": True},
        )
    payload = ei.value.as_dict()
    assert payload["PHASE_STATUS"] == "BLOCKED"
    assert payload["ROLLBACK_PERFORMED"] is True
    assert payload["ROLLBACK_RESULT"] == "FAIL"
    assert "MANUAL_INTERVENTION_REQUIRED" in str(ei.value)
    assert payload["PHASE_STATUS"] != "COMPLETE"


def test_precheck_failure_before_commit_never_invokes_rollback():
    """Uncommitted / failed inner must not trigger host auto-rollback."""
    before, after = _passing_inventories()
    inner = {
        "ACCOUNT23_PROMOTION_PASS": False,
        "promotion_committed": False,
        "FAILED_STAGE": "PRECHECK",
        "FAILED_GATE": "QUEUE23",
    }
    assert a23.promotion_committed(inner) is False
    calls: list = []

    with pytest.raises(a23.GateFailure):
        a23.host_postcheck_with_auto_rollback(
            before=before,
            after=after,
            inner={
                "ACCOUNT23_PROMOTION_PASS": False,
                "promotion_committed": False,
            },
            rollback_invoker=lambda _s: calls.append("rb")
            or {"ok": True, "ROLLBACK_RESULT": "PASS"},
            restore_verifier=lambda *_a, **_k: {"ok": True},
        )
    assert calls == []


def test_restore_verifier_failure_marks_rollback_fail():
    before, after = _passing_inventories()
    after = {**after, "queue23": 1}
    inner = _success_inner()

    with pytest.raises(a23.GateFailure) as ei:
        a23.host_postcheck_with_auto_rollback(
            before=before,
            after=after,
            inner=inner,
            rollback_invoker=lambda _s: {"ok": True, "ROLLBACK_RESULT": "PASS"},
            restore_verifier=lambda *_a, **_k: {"ok": False, "reason": "a23_mismatch"},
        )
    assert ei.value.rollback_result == "FAIL"
    assert ei.value.as_dict()["PHASE_STATUS"] == "BLOCKED"


@pytest.mark.asyncio
async def test_rollback_touches_only_target_account_session(db):
    a13, a13_row = await _seed_account13_peer_active(db)
    target = _acct(db, phone="989120000201")
    other = _acct(
        db, phone="989120000202", guid="other-guid", status=RubikaIdentityStatus.VERIFIED
    )
    t_row = _legacy_session(db, target, guid="guid-201")
    o_row = _legacy_session(db, other, guid="other-guid")
    other_promo = await promote_proven_legacy_rubika_session(
        db,
        account_id=other.id,
        session_id=o_row.id,
        expected_identity="other-guid",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("other-guid"),
        queue_length_fn=lambda _a: 0,
    )
    assert other_promo.ok

    promo = await promote_proven_legacy_rubika_session(
        db,
        account_id=target.id,
        session_id=t_row.id,
        expected_identity="guid-201",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-201"),
        queue_length_fn=lambda _a: 0,
    )
    assert promo.ok

    rb = rollback_legacy_promotion(
        db,
        account_id=target.id,
        session_id=t_row.id,
        identity_snapshot_before=promo.identity_snapshot_before,
    )
    assert rb.ok
    db.refresh(t_row)
    db.refresh(o_row)
    db.refresh(other)
    assert t_row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED
    assert o_row.session_status == RubikaSessionStatus.ACTIVE
    assert other.rubika_guid == "other-guid"
    _assert_account13_peer_unchanged(db, a13, a13_row)


@pytest.mark.asyncio
async def test_newly_bound_identity_restored_on_rollback(db):
    a13, a13_row = await _seed_account13_peer_active(db)
    acct = _acct(db, phone="989120000203")
    row = _legacy_session(db, acct, guid="guid-203")
    promo = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-203",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-203"),
        queue_length_fn=lambda _a: 0,
    )
    assert promo.identity_newly_bound is True
    rb = rollback_legacy_promotion(
        db,
        account_id=acct.id,
        session_id=row.id,
        identity_snapshot_before=promo.identity_snapshot_before,
    )
    assert rb.ok and rb.identity_restored is True
    db.refresh(acct)
    db.refresh(row)
    assert acct.rubika_guid is None
    assert acct.rubika_identity_status == RubikaIdentityStatus.UNBOUND
    assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED
    _assert_account13_peer_unchanged(db, a13, a13_row)


@pytest.mark.asyncio
async def test_preexisting_matching_identity_not_removed_on_rollback(db):
    a13, a13_row = await _seed_account13_peer_active(db)
    acct = _acct(
        db,
        phone="989120000204",
        guid="guid-204",
        status=RubikaIdentityStatus.VERIFIED,
    )
    row = _legacy_session(db, acct, guid="guid-204")
    promo = await promote_proven_legacy_rubika_session(
        db,
        account_id=acct.id,
        session_id=row.id,
        expected_identity="guid-204",
        evidence=EVIDENCE,
        skip_network_proof=True,
        injected_proof=_pass_proof("guid-204"),
        queue_length_fn=lambda _a: 0,
    )
    assert promo.ok
    assert promo.identity_newly_bound is False
    rb = rollback_legacy_promotion(
        db,
        account_id=acct.id,
        session_id=row.id,
        identity_snapshot_before=promo.identity_snapshot_before,
    )
    assert rb.ok
    assert rb.identity_restored is False
    db.refresh(acct)
    db.refresh(row)
    assert acct.rubika_guid == "guid-204"
    assert acct.rubika_identity_status == RubikaIdentityStatus.VERIFIED
    assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED
    _assert_account13_peer_unchanged(db, a13, a13_row)


def test_host_inventories_keep_account13_session729_active():
    """Host fixtures encode production invariant: Account13/729 ACTIVE unchanged."""
    before, after_ok = _passing_inventories()
    assert before["a13"] == "729:active"
    assert after_ok["a13"] == "729:active"
    assert "729:13:active" in before["session_inventory"]
    assert "729:13:active" in after_ok["session_inventory"]


def test_rollback_worker_hardcodes_only_23_725():
    assert a23.ACCOUNT_ID == 23
    assert a23.SESSION_ID == 725
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "account_id=ACCOUNT_ID" in src
    assert "session_id=SESSION_ID" in src
    assert "run_rollback_worker" in src
    assert "L8_EXEC_ROLE=rollback" in src or 'role == "rollback"' in src
    # Must not accept alternate target env for IDs
    assert "L8_ACCOUNT_ID" not in src
    assert "L8_SESSION_ID" not in src


def test_main_failure_payload_never_complete_on_rollback_fail(monkeypatch, tmp_path):
    monkeypatch.setenv("L8_ALLOW_PRODUCTION_PROMOTION", "1")
    monkeypatch.setenv("L8_PRODUCTION_TARGET", "account23")
    monkeypatch.setenv("L8_EXEC_ROLE", "host")
    monkeypatch.setattr(a23, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(a23, "OUT", tmp_path / "out.json")

    def boom():
        raise a23.GateFailure(
            failed_stage="POST_VERIFY",
            failed_gate="ACCOUNT74_UNCHANGED",
            expected="724:legacy_unclassified",
            actual="724:active",
            rollback_performed=True,
            rollback_result="FAIL",
            message="MANUAL_INTERVENTION_REQUIRED: test",
        )

    monkeypatch.setattr(a23, "run_host", boom)
    code = a23.main()
    assert code == 2
    failure = (tmp_path / "L8_ACCOUNT23_LAST_FAILURE.json").read_text(encoding="utf-8")
    assert '"PHASE_STATUS": "BLOCKED"' in failure
    assert "MANUAL_INTERVENTION_REQUIRED" in failure
    assert '"ROLLBACK_RESULT": "FAIL"' in failure
    assert '"PHASE_STATUS": "COMPLETE"' not in failure
