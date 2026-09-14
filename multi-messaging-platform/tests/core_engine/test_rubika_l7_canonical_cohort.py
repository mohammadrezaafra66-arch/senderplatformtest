"""L7 — Canonical runtime cohort gating tests (isolated / hermetic)."""

from __future__ import annotations

from unittest.mock import MagicMock

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
from core_engine.services.rubika_canonical_runtime import (
    MALFORMED_ALLOWLIST,
    MODE_OFF,
    SHADOW_NO_ACTIVE,
    clear_shadow_hooks,
    enforce_applies_to_account,
    get_shadow_metrics,
    load_rubika_runtime_session,
    parse_canonical_account_allowlist,
    register_shadow_hook,
    select_legacy_rubika_session_row,
)
from core_engine.services.rubika_canonical_session import (
    MULTIPLE_ACTIVE_SESSIONS,
    NO_ACTIVE_SESSION,
    CanonicalSessionError,
)
from core_engine.services.rubika_l7_classifiers import (
    classify_account12,
    classify_duplicate_account,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.errors import SessionInvalidError
from workers.session_access import load_account_session_plaintext


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
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


def _make_account(db, *, phone_suffix: str = "100") -> Account:
    acct = Account(
        platform=PlatformType.RUBIKA,
        label=f"l7-{phone_suffix}",
        phone_number=f"98912{phone_suffix}000",
        status=AccountStatus.ACTIVE,
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
        rubika_guid=f"guid-{phone_suffix}",
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct


def _store_session(
    db,
    account: Account,
    *,
    status: RubikaSessionStatus = RubikaSessionStatus.LEGACY_UNCLASSIFIED,
    guid: str | None = None,
) -> ChannelSession:
    g = guid or account.rubika_guid or "guid-x"
    envelope = build_session_envelope(
        phone_number=account.phone_number or "989120000000",
        auth="auth-token-value",
        guid=g,
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/fake\n-----END RSA PRIVATE KEY-----",
    )
    row = store_channel_session(
        db,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=envelope,
        session_status=status,
        identity_guid=g,
    )
    db.commit()
    db.refresh(row)
    return row


# --- unit: allowlist / mode ---


def test_parse_empty_allowlist_ok():
    result = parse_canonical_account_allowlist("")
    assert result.ok
    assert result.account_ids == frozenset()


def test_malformed_allowlist_fails_safely():
    result = parse_canonical_account_allowlist("13,abc,74")
    assert not result.ok
    assert result.account_ids == frozenset()
    assert result.error and MALFORMED_ALLOWLIST in result.error


def test_empty_allowlist_enforce_affects_nobody(monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "enforce")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "")
    get_settings.cache_clear()
    assert enforce_applies_to_account(13) is False
    assert enforce_applies_to_account(74) is False


def test_malformed_allowlist_enforce_affects_nobody(monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "enforce")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "13,nope")
    get_settings.cache_clear()
    assert enforce_applies_to_account(13) is False


def test_v1_false_default_mode_off_legacy_compatible(monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "false")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "off")
    get_settings.cache_clear()
    assert get_settings().RUBIKA_CANONICAL_SESSION_MODE == MODE_OFF
    assert get_settings().RUBIKA_CANONICAL_SESSION_V1 is False


def test_mode_selection_does_not_import_otp_or_send():
    """Mode helpers must not pull login/send modules (guardrail)."""
    import core_engine.services.rubika_canonical_runtime as runtime

    src = open(runtime.__file__, encoding="utf-8").read()
    assert "request_rubika_login" not in src
    assert "send_message" not in src
    assert "submit_rubika_login_code" not in src


# --- DB-backed ---


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
    finally:
        session.close()


def test_mode_off_uses_legacy_path(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "off")
    get_settings.cache_clear()
    acct = _make_account(db, phone_suffix="201")
    older = _store_session(db, acct)
    newer = _store_session(db, acct)
    assert newer.id > older.id
    selected = load_rubika_runtime_session(db, acct.id)
    assert selected.source == "legacy"
    assert selected.session_id == newer.id
    plain = load_account_session_plaintext(
        db, account_id=acct.id, session_type=SessionType.RUBIKA_SESSION
    )
    assert plain  # decrypt ok via legacy


def test_shadow_does_not_affect_runtime_selection(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "shadow")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "")
    get_settings.cache_clear()
    acct = _make_account(db, phone_suffix="202")
    # Put allowlist after account created
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(acct.id))
    get_settings.cache_clear()
    legacy_row = _store_session(db, acct)  # legacy_unclassified only
    selected = load_rubika_runtime_session(db, acct.id)
    assert selected.source == "legacy"
    assert selected.session_id == legacy_row.id
    metrics = get_shadow_metrics()
    assert metrics.counts[SHADOW_NO_ACTIVE] >= 1
    assert metrics.last is not None
    assert metrics.last.match is False


def test_shadow_performs_no_db_writes(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "shadow")
    acct = _make_account(db, phone_suffix="203")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(acct.id))
    get_settings.cache_clear()
    row = _store_session(db, acct)
    before_status = row.session_status
    before_updated = row.updated_at
    # Wrap commit to detect writes after baseline
    commits = {"n": 0}
    original_commit = db.commit

    def _counting_commit():
        commits["n"] += 1
        return original_commit()

    db.commit = _counting_commit  # type: ignore[method-assign]
    load_rubika_runtime_session(db, acct.id)
    db.refresh(row)
    assert row.session_status == before_status
    assert row.updated_at == before_updated
    assert commits["n"] == 0


def test_shadow_performs_no_redis_writes(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "shadow")
    acct = _make_account(db, phone_suffix="204")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(acct.id))
    get_settings.cache_clear()
    _store_session(db, acct)
    redis_calls: list[str] = []

    class _FakeRedis:
        def __getattr__(self, name):
            def _method(*_a, **_k):
                redis_calls.append(name)
                return None

            return _method

    monkeypatch.setattr(
        "core_engine.services.redis_client.get_redis_client",
        lambda: _FakeRedis(),
        raising=False,
    )
    load_rubika_runtime_session(db, acct.id)
    assert redis_calls == []


def test_enforce_affects_allowlisted_only(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "enforce")
    in_acct = _make_account(db, phone_suffix="205")
    out_acct = _make_account(db, phone_suffix="206")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(in_acct.id))
    get_settings.cache_clear()
    # Allowlisted: no ACTIVE → fail closed
    _store_session(db, in_acct)
    with pytest.raises(CanonicalSessionError) as exc:
        load_rubika_runtime_session(db, in_acct.id)
    assert exc.value.code == NO_ACTIVE_SESSION
    # Non-allowlisted: legacy still works
    out_row = _store_session(db, out_acct)
    selected = load_rubika_runtime_session(db, out_acct.id)
    assert selected.source == "legacy"
    assert selected.session_id == out_row.id


def test_enforce_canonical_failure_no_max_id_fallback(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "enforce")
    acct = _make_account(db, phone_suffix="207")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(acct.id))
    get_settings.cache_clear()
    legacy = _store_session(db, acct)  # only legacy_unclassified
    assert select_legacy_rubika_session_row(db, acct.id).id == legacy.id
    with pytest.raises(CanonicalSessionError) as exc:
        load_rubika_runtime_session(db, acct.id)
    assert exc.value.code == NO_ACTIVE_SESSION
    # Worker wrapper also fails closed
    with pytest.raises(SessionInvalidError):
        load_account_session_plaintext(
            db, account_id=acct.id, session_type=SessionType.RUBIKA_SESSION
        )


def test_enforce_uses_active_not_max_id(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "enforce")
    acct = _make_account(db, phone_suffix="208")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(acct.id))
    get_settings.cache_clear()
    older = _store_session(db, acct, status=RubikaSessionStatus.ACTIVE)
    newer = _store_session(db, acct, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    assert newer.id > older.id
    selected = load_rubika_runtime_session(db, acct.id)
    assert selected.source == "canonical_enforce"
    assert selected.session_id == older.id  # ACTIVE, not max(id)


def test_duplicate_canonical_active_hard_fails(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "enforce")
    acct = _make_account(db, phone_suffix="209")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(acct.id))
    get_settings.cache_clear()
    _store_session(db, acct, status=RubikaSessionStatus.ACTIVE)
    _store_session(db, acct, status=RubikaSessionStatus.ACTIVE)
    with pytest.raises(CanonicalSessionError) as exc:
        load_rubika_runtime_session(db, acct.id)
    assert exc.value.code == MULTIPLE_ACTIVE_SESSIONS


def test_shadow_comparison_emits_safe_metadata_only(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_MODE", "shadow")
    acct = _make_account(db, phone_suffix="210")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", str(acct.id))
    get_settings.cache_clear()
    row = _store_session(db, acct)
    captured: list[dict] = []
    register_shadow_hook(lambda c: captured.append(c.as_safe_dict()))
    load_rubika_runtime_session(db, acct.id)
    assert len(captured) == 1
    payload = captured[0]
    assert set(payload.keys()) == {
        "account_id",
        "legacy_selected_session_id",
        "canonical_selected_session_id",
        "canonical_error",
        "match",
        "metric",
    }
    blob = str(payload)
    assert "auth" not in blob.lower() or payload["canonical_error"]
    assert "private_key" not in blob
    assert payload["legacy_selected_session_id"] == row.id


def test_account12_shaped_duplicate_remains_ambiguous():
    rows = [
        {
            "session_id": 657,
            "decrypt_status": "OK",
            "structure_status": "OK",
            "reconnect_status": "AUTH_RECONNECT_PASS",
            "identity_match": True,
            "identity_guid": "same-guid",
        },
        {
            "session_id": 728,
            "decrypt_status": "OK",
            "structure_status": "OK",
            "reconnect_status": "AUTH_RECONNECT_PASS",
            "identity_match": True,
            "identity_guid": "same-guid",
        },
    ]
    assert classify_account12(rows) == "BOTH_VALID_CURRENT_IDENTITY"
    assert classify_duplicate_account(rows) == "BOTH_VALID_SAME_IDENTITY"


def test_classify_one_proven_one_invalid():
    rows = [
        {
            "session_id": 721,
            "decrypt_status": "OK",
            "structure_status": "OK",
            "reconnect_status": "AUTH_RECONNECT_PASS",
            "identity_match": True,
            "identity_guid": "g",
        },
        {
            "session_id": 2,
            "decrypt_status": "SESSION_DECRYPT_FAILED",
            "structure_status": "N/A",
            "reconnect_status": "SKIPPED",
            "identity_match": None,
        },
    ]
    assert classify_duplicate_account(rows) == "ONE_PROVEN_ONE_INVALID"
