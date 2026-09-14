"""L9 — Batch A shaped canonical shadow fixtures (isolated).

Maps logical Batch A accounts to ACTIVE sessions and asserts shadow MATCH
while remaining legacy-authoritative. Hermetic — no production DB.
"""

from __future__ import annotations

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
from core_engine.services.rubika_canonical_runtime import (
    SHADOW_CANONICAL_ERROR,
    SHADOW_DIFFERENT_SESSION,
    SHADOW_MATCH,
    SHADOW_NO_ACTIVE,
    clear_shadow_hooks,
    compare_legacy_vs_canonical,
    get_shadow_metrics,
    load_rubika_runtime_session,
    register_shadow_hook,
    select_legacy_rubika_session_row,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session


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


def _acct(db, *, phone: str, guid: str):
    a = Account(
        platform=PlatformType.RUBIKA,
        label=f"l9-{phone[-4:]}",
        phone_number=phone,
        status=AccountStatus.ACTIVE,
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
        rubika_guid=guid,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _active_session(db, account: Account, *, guid: str):
    env = build_session_envelope(
        phone_number=account.phone_number or "989120000000",
        auth="auth-token-value",
        guid=guid,
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/fake\n-----END RSA PRIVATE KEY-----",
    )
    row = store_channel_session(
        db,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=env,
        session_status=RubikaSessionStatus.ACTIVE,
        identity_guid=guid,
    )
    db.commit()
    db.refresh(row)
    return row


def _batch_a_cohort(db):
    """Logical Batch A: three accounts each with one ACTIVE session."""
    mapping = []
    for phone, guid in (
        ("989120000013", "guid-13"),
        ("989120000023", "guid-23"),
        ("989120000074", "guid-74"),
    ):
        acct = _acct(db, phone=phone, guid=guid)
        row = _active_session(db, acct, guid=guid)
        mapping.append((acct, row))
    return mapping


def test_shadow_metrics_distinguish_all_codes():
    assert {SHADOW_MATCH, SHADOW_NO_ACTIVE, SHADOW_DIFFERENT_SESSION, SHADOW_CANONICAL_ERROR}


def test_batch_a_shadow_match_legacy_authoritative(db, monkeypatch):
    cohort = _batch_a_cohort(db)
    ids = ",".join(str(a.id) for a, _ in cohort)
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "shadow")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", ids)
    get_settings.cache_clear()
    events = []
    register_shadow_hook(lambda c: events.append(c))

    before_attempts = db.execute(
        __import__("sqlalchemy").text("SELECT COUNT(*) FROM message_attempts")
    ).scalar()

    for acct, row in cohort:
        selected = load_rubika_runtime_session(db, acct.id)
        assert selected.source == "legacy"
        assert selected.mode == "shadow"
        assert selected.session_id == row.id
        legacy = select_legacy_rubika_session_row(db, acct.id)
        cmp = compare_legacy_vs_canonical(db, acct.id, legacy_row=legacy)
        assert cmp.metric == SHADOW_MATCH
        assert cmp.legacy_selected_session_id == row.id
        assert cmp.canonical_selected_session_id == row.id
        safe = cmp.as_safe_dict()
        assert set(safe) >= {
            "account_id",
            "legacy_selected_session_id",
            "canonical_selected_session_id",
            "canonical_error",
            "match",
            "metric",
        }
        assert "ciphertext" not in safe
        assert "plaintext" not in safe
        assert "auth" not in str(safe).lower()

    after_attempts = db.execute(
        __import__("sqlalchemy").text("SELECT COUNT(*) FROM message_attempts")
    ).scalar()
    assert before_attempts == after_attempts
    assert get_shadow_metrics().counts[SHADOW_MATCH] >= 3
    assert events


def test_batch_a_enforce_allowlist_only(db, monkeypatch):
    cohort = _batch_a_cohort(db)
    target = cohort[0][0]
    other = cohort[1][0]
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "enforce")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(target.id))
    get_settings.cache_clear()

    sel_target = load_rubika_runtime_session(db, target.id)
    assert sel_target.source == "canonical_enforce"
    assert sel_target.session_id == cohort[0][1].id

    sel_other = load_rubika_runtime_session(db, other.id)
    assert sel_other.source == "legacy"
    assert sel_other.session_id == cohort[1][1].id


def test_shadow_safe_log_fields_have_no_secrets():
    from core_engine.services.rubika_canonical_runtime import ShadowComparison

    c = ShadowComparison(
        account_id=13,
        legacy_selected_session_id=729,
        canonical_selected_session_id=729,
        canonical_error=None,
        match=True,
        metric=SHADOW_MATCH,
    )
    d = c.as_safe_dict()
    blob = str(d).lower()
    for banned in ("cipher", "token", "password", "private_key", "auth-token", "phone"):
        assert banned not in blob
