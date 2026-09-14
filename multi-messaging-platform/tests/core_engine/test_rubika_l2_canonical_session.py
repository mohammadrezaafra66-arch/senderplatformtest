"""L2 — Canonical Rubika session schema / loader / promotion (isolated DB only)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
from core_engine.services.rubika_canonical_session import (
    MULTIPLE_ACTIVE_SESSIONS,
    NO_ACTIVE_SESSION,
    CanonicalSessionError,
    load_canonical_rubika_session,
)
from core_engine.services.rubika_identity import (
    IDENTITY_BOUND,
    IDENTITY_MISMATCH,
    IDENTITY_OK,
    bind_or_verify_rubika_identity,
)
from core_engine.services.rubika_session_migration_guards import (
    MigrationPreconditionError,
    assert_refuses_auto_canonicalize_ambiguous_duplicates,
)
from core_engine.services.rubika_session_promotion import (
    CANDIDATE_WRONG_STATUS,
    PROMOTION_OK,
    promote_validated_session,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session


@pytest.fixture(autouse=True)
def session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def ensure_active_unique_index(pg_engine, pg_session_factory):
    """Create L2 partial unique index after create_all (enum-native compare)."""
    _ = pg_session_factory  # ensure tables exist first
    with pg_engine.connect() as conn:
        # Prior tests may leave ACTIVE rows; clear before creating unique index.
        conn.execute(
            text(
                "UPDATE channel_sessions SET session_status = 'legacy_unclassified' "
                "WHERE session_status = 'active'"
            )
        )
        labels = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON e.enumtypid=t.oid WHERE t.typname='sessiontype'"
                )
            ).fetchall()
        ]
        rubika_labels = [x for x in labels if "rubika" in x.lower()]
        if not rubika_labels:
            rubika_labels = ["rubika_session", "RUBIKA_SESSION"]
        preds = " OR ".join(f"session_type = '{lab}'" for lab in sorted(set(rubika_labels)))
        conn.execute(text("DROP INDEX IF EXISTS uq_channel_sessions_one_active_rubika"))
        conn.execute(
            text(
                f"""
                CREATE UNIQUE INDEX uq_channel_sessions_one_active_rubika
                ON channel_sessions (account_id)
                WHERE session_status = 'active' AND ({preds})
                """
            )
        )
        conn.commit()
    yield


def _envelope(guid: str = "guid-aaa", phone: str = "989120000001") -> str:
    return build_session_envelope(
        phone_number=phone,
        auth="auth-token-value",
        guid=guid,
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/fake\n-----END RSA PRIVATE KEY-----",
    )


def _acct(session, *, label="l2", phone="989120000099"):
    a = Account(
        platform=PlatformType.RUBIKA,
        phone_number=phone,
        label=label,
        status=AccountStatus.ACTIVE,
        rubika_identity_status=RubikaIdentityStatus.UNBOUND,
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def _store(session, account_id, *, status, guid="guid-aaa", phone="989120000001"):
    return store_channel_session(
        session,
        account_id=account_id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=_envelope(guid=guid, phone=phone),
        session_status=status,
        identity_guid=guid,
    )


def test_legacy_rows_default_unclassified(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = store_channel_session(
        db,
        account_id=acct.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=_envelope(),
    )
    db.commit()
    db.refresh(row)
    assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED


def test_zero_active_raises(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    _store(db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    db.commit()
    with pytest.raises(CanonicalSessionError) as ei:
        load_canonical_rubika_session(db, acct.id, require_identity_binding=False)
    assert ei.value.code == NO_ACTIVE_SESSION


def test_one_active_loads(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    bind_or_verify_rubika_identity(acct, "guid-aaa")
    row = _store(db, acct.id, status=RubikaSessionStatus.ACTIVE)
    db.commit()
    loaded = load_canonical_rubika_session(db, acct.id)
    assert loaded.session_id == row.id
    assert loaded.identity_guid == "guid-aaa"


def test_multiple_active_hard_fail(pg_session_factory, pg_engine):
    db = pg_session_factory()
    acct = _acct(db)
    r1 = _store(db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    r2 = _store(db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    db.commit()
    with pg_engine.connect() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_channel_sessions_one_active_rubika"))
        conn.execute(
            text(
                "UPDATE channel_sessions SET session_status = 'active' "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": [r1.id, r2.id]},
        )
        conn.commit()
    with pytest.raises(CanonicalSessionError) as ei:
        load_canonical_rubika_session(db, acct.id, require_identity_binding=False)
    assert ei.value.code == MULTIPLE_ACTIVE_SESSIONS


def test_loader_never_chooses_max_id(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    bind_or_verify_rubika_identity(acct, "guid-old")
    older = _store(db, acct.id, status=RubikaSessionStatus.ACTIVE, guid="guid-old")
    newer = _store(
        db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="guid-new"
    )
    db.commit()
    assert newer.id > older.id
    loaded = load_canonical_rubika_session(db, acct.id)
    assert loaded.session_id == older.id
    assert loaded.identity_guid == "guid-old"


def test_legacy_not_runtime_loadable(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    _store(db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    db.commit()
    with pytest.raises(CanonicalSessionError) as ei:
        load_canonical_rubika_session(db, acct.id, require_identity_binding=False)
    assert ei.value.code == NO_ACTIVE_SESSION


def test_validating_not_runtime_loadable(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    _store(db, acct.id, status=RubikaSessionStatus.VALIDATING)
    db.commit()
    with pytest.raises(CanonicalSessionError) as ei:
        load_canonical_rubika_session(db, acct.id, require_identity_binding=False)
    assert ei.value.code == NO_ACTIVE_SESSION


def test_promotion_supersedes_old_active(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    old = _store(db, acct.id, status=RubikaSessionStatus.ACTIVE, guid="guid-aaa")
    cand = _store(db, acct.id, status=RubikaSessionStatus.VALIDATING, guid="guid-aaa")
    db.commit()
    bind_or_verify_rubika_identity(acct, "guid-aaa")
    db.commit()
    result = promote_validated_session(
        db, account_id=acct.id, candidate_session_id=cand.id
    )
    db.commit()
    assert result.ok and result.code == PROMOTION_OK
    db.refresh(old)
    db.refresh(cand)
    assert old.session_status == RubikaSessionStatus.SUPERSEDED
    assert cand.session_status == RubikaSessionStatus.ACTIVE
    loaded = load_canonical_rubika_session(db, acct.id)
    assert loaded.session_id == cand.id


def test_failed_promotion_leaves_old_active(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    bind_or_verify_rubika_identity(acct, "guid-aaa")
    old = _store(db, acct.id, status=RubikaSessionStatus.ACTIVE, guid="guid-aaa")
    # Candidate not VALIDATING
    bad = _store(db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="guid-aaa")
    db.commit()
    result = promote_validated_session(
        db, account_id=acct.id, candidate_session_id=bad.id
    )
    assert not result.ok
    assert result.code == CANDIDATE_WRONG_STATUS
    db.refresh(old)
    assert old.session_status == RubikaSessionStatus.ACTIVE


def test_identity_mismatch_blocks_promotion(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    bind_or_verify_rubika_identity(acct, "guid-bound")
    old = _store(db, acct.id, status=RubikaSessionStatus.ACTIVE, guid="guid-bound")
    cand = _store(db, acct.id, status=RubikaSessionStatus.VALIDATING, guid="guid-other")
    db.commit()
    result = promote_validated_session(
        db, account_id=acct.id, candidate_session_id=cand.id
    )
    assert not result.ok
    assert result.code == IDENTITY_MISMATCH
    db.refresh(old)
    db.refresh(cand)
    assert old.session_status == RubikaSessionStatus.ACTIVE
    assert cand.session_status == RubikaSessionStatus.VALIDATING


def test_account_guid_cannot_silently_change(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    r1 = bind_or_verify_rubika_identity(acct, "guid-1")
    assert r1.ok and r1.code == IDENTITY_BOUND
    r2 = bind_or_verify_rubika_identity(acct, "guid-2")
    assert not r2.ok and r2.code == IDENTITY_MISMATCH
    assert acct.rubika_guid == "guid-1"
    assert acct.rubika_identity_status == RubikaIdentityStatus.MISMATCH_LOCKED
    r3 = bind_or_verify_rubika_identity(acct, "guid-1")
    assert r3.ok and r3.code == IDENTITY_OK


def test_partial_unique_prevents_two_active(pg_session_factory, ensure_active_unique_index):
    db = pg_session_factory()
    acct = _acct(db)
    _store(db, acct.id, status=RubikaSessionStatus.ACTIVE, guid="guid-aaa")
    db.commit()
    with pytest.raises(IntegrityError):
        _store(db, acct.id, status=RubikaSessionStatus.ACTIVE, guid="guid-aaa")
        db.commit()
    db.rollback()


def test_account_12_79_legacy_fixture_not_auto_promoted(pg_session_factory):
    """Protected accounts shaped like production remain LEGACY until L16."""
    db = pg_session_factory()
    for aid_label, phone in (("acct-12", "989120000012"), ("acct-79", "989120000079")):
        acct = _acct(db, label=aid_label, phone=phone)
        row = _store(db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
        db.commit()
        assert row.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED
        with pytest.raises(CanonicalSessionError) as ei:
            load_canonical_rubika_session(db, acct.id, require_identity_binding=False)
        assert ei.value.code == NO_ACTIVE_SESSION


def test_migration_precondition_refuses_ambiguous_duplicates(pg_session_factory, pg_engine):
    db = pg_session_factory()
    acct = _acct(db)
    _store(db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    _store(db, acct.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    db.commit()
    with pg_engine.connect() as conn:
        inv = assert_refuses_auto_canonicalize_ambiguous_duplicates(
            conn, allow_duplicates_as_legacy=True
        )
        assert acct.id in inv.account_ids_with_multiple_sessions
        with pytest.raises(MigrationPreconditionError):
            assert_refuses_auto_canonicalize_ambiguous_duplicates(
                conn, allow_duplicates_as_legacy=False
            )
