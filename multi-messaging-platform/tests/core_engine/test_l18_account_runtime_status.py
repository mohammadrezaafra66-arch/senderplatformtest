"""L18 isolated tests — account runtime status truth (enabled != connected)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
    run_connection_test,
    runtime_status_label,
)
from core_engine.services.rubika_canonical_session import CanonicalSessionError
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _l18_env(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_SCOPE", "canonical_active")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _acct(db, *, platform=PlatformType.RUBIKA, status=AccountStatus.ACTIVE, phone="989140000001", **kw) -> Account:
    a = Account(
        platform=platform,
        phone_number=phone,
        label=kw.get("label", "l18"),
        status=status,
        rubika_guid=kw.get("rubika_guid"),
        rubika_identity_status=kw.get(
            "rubika_identity_status", RubikaIdentityStatus.UNBOUND
        ),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _rubika_session(db, account, *, status=RubikaSessionStatus.ACTIVE, guid: str | None = None):
    g = guid or f"guid-{account.id}"
    env = build_session_envelope(
        phone_number=account.phone_number or "989140000000",
        auth="auth-l18",
        guid=g,
        user_agent="ua",
        private_key="pk",
    )
    return store_channel_session(
        db,
        account_id=int(account.id),
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=env,
        session_status=status,
        identity_guid=g,
    )


def _challenge(db, account_id: int, *, state, expires_in: int | None = 600) -> RubikaLoginChallenge:
    now = datetime.now(timezone.utc)
    ch = RubikaLoginChallenge(
        id=f"ch-{account_id}-{state.value}-{int(now.timestamp())}",
        account_id=account_id,
        state=state,
        requested_at=now,
        expires_at=(now + timedelta(seconds=expires_in)) if expires_in is not None else None,
    )
    db.add(ch)
    db.commit()
    return ch


def test_1_enabled_is_not_connected(db):
    a = _acct(db, status=AccountStatus.ACTIVE)
    r = compute_account_runtime_status(db, a, worker_covered=False)
    assert a.status == AccountStatus.ACTIVE
    assert r.enabled is True
    assert r.runtime_status == RuntimeStatus.LOGIN_REQUIRED.value
    assert r.runtime_status_label == "نیاز به ورود"


def test_2_zero_session_login_required(db):
    a = _acct(db)
    r = compute_account_runtime_status(db, a)
    assert r.runtime_status == RuntimeStatus.LOGIN_REQUIRED.value


def test_3_active_otp_waiting(db):
    a = _acct(db)
    _challenge(db, a.id, state=RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR)
    r = compute_account_runtime_status(db, a)
    assert r.runtime_status == RuntimeStatus.OTP_WAITING.value


def test_4_expired_otp_becomes_login_required(db):
    a = _acct(db)
    _challenge(db, a.id, state=RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR, expires_in=-30)
    r = compute_account_runtime_status(db, a)
    assert r.runtime_status == RuntimeStatus.LOGIN_REQUIRED.value


def test_5_canonical_ready_with_worker(db):
    a = _acct(
        db,
        phone="989140000027",
        rubika_guid="guid-27",
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
        label="a27",
    )
    _rubika_session(db, a, guid="guid-27")
    with patch(
        "core_engine.services.rubika_canonical_session.load_canonical_rubika_session",
        return_value=object(),
    ):
        r = compute_account_runtime_status(
            db, a, worker_covered=True, dispatch_eligible_ids={a.id}
        )
    assert r.runtime_status == RuntimeStatus.READY.value
    assert r.dispatch_ready is True


def test_6_canonical_no_worker(db):
    a = _acct(
        db,
        phone="989140000013",
        rubika_guid="guid-13",
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
    )
    _rubika_session(db, a, guid="guid-13")
    with patch(
        "core_engine.services.rubika_canonical_session.load_canonical_rubika_session",
        return_value=object(),
    ):
        r = compute_account_runtime_status(
            db, a, worker_covered=False, dispatch_eligible_ids={a.id}
        )
    assert r.runtime_status == RuntimeStatus.AUTHENTICATED_NO_WORKER.value


def test_7_duplicate_legacy_manual_review(db):
    a = _acct(db, phone="989140000012", label="a12")
    _rubika_session(db, a, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="g1")
    _rubika_session(db, a, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="g2")
    r = compute_account_runtime_status(db, a, worker_covered=True)
    assert r.runtime_status == RuntimeStatus.MANUAL_REVIEW.value
    assert r.runtime_status != RuntimeStatus.READY.value


def test_8_decrypt_failure_session_error(db):
    a = _acct(
        db,
        rubika_guid="guid-x",
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
    )
    _rubika_session(db, a, guid="guid-x")
    with patch(
        "core_engine.services.rubika_canonical_session.load_canonical_rubika_session",
        side_effect=CanonicalSessionError("DECRYPT", "fail"),
    ), patch(
        "core_engine.services.account_runtime_status._try_decrypt_session",
        return_value=(False, "SESSION_DECRYPT_FAILED"),
    ):
        r = compute_account_runtime_status(db, a, worker_covered=True)
    assert r.runtime_status == RuntimeStatus.SESSION_ERROR.value


def test_9_reconnect_failure_connection_error(db):
    a = _acct(
        db,
        rubika_guid="guid-y",
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
    )
    _rubika_session(db, a, guid="guid-y")
    with patch(
        "core_engine.services.rubika_canonical_session.load_canonical_rubika_session",
        side_effect=CanonicalSessionError("IDENTITY_MISMATCH", "fail"),
    ), patch(
        "core_engine.services.account_runtime_status._try_decrypt_session",
        return_value=(True, None),
    ):
        r = compute_account_runtime_status(db, a, worker_covered=True)
    assert r.runtime_status == RuntimeStatus.CONNECTION_ERROR.value


def test_10_disabled_resting(db):
    a = _acct(db, status=AccountStatus.RESTING)
    r = compute_account_runtime_status(db, a)
    assert r.runtime_status == RuntimeStatus.DISABLED.value


def test_11_account27_shaped_ready(db):
    a = _acct(
        db,
        phone="989140000127",
        rubika_guid="guid-27b",
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
        label="a27",
    )
    _rubika_session(db, a, guid="guid-27b")
    with patch(
        "core_engine.services.rubika_canonical_session.load_canonical_rubika_session",
        return_value=object(),
    ):
        r = compute_account_runtime_status(
            db, a, worker_covered=True, dispatch_eligible_ids={a.id}
        )
    assert r.runtime_status == RuntimeStatus.READY.value
    assert runtime_status_label(r.runtime_status) == "آماده ارسال"


def test_12_account12_shaped_ambiguous(db):
    a = _acct(db, phone="989140000112", label="a12")
    _rubika_session(db, a, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="657")
    _rubika_session(db, a, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="728")
    r = compute_account_runtime_status(db, a, worker_covered=True)
    assert r.runtime_status == RuntimeStatus.MANUAL_REVIEW.value


def test_13_account79_legacy_fixture(db):
    a = _acct(db, phone="989140000179", label="a79")
    row = _rubika_session(db, a, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="g79")
    with patch(
        "core_engine.services.account_runtime_status._try_decrypt_session",
        return_value=(True, None),
    ):
        r = compute_account_runtime_status(
            db, a, worker_covered=True, dispatch_eligible_ids={a.id}
        )
    assert r.runtime_status == RuntimeStatus.READY.value
    assert r.details.get("legacy") is True
    assert r.details.get("session_id") == row.id


def test_14_no_session_future_account(db):
    a = _acct(db, phone="989140000028", label="a28")
    r = compute_account_runtime_status(db, a)
    assert r.runtime_status == RuntimeStatus.LOGIN_REQUIRED.value
    assert r.operator_action_code == "LOGIN"


def test_15_16_connection_test_never_sends_or_otp(db):
    a = _acct(db, phone="989140000099")
    with patch(
        "core_engine.services.account_runtime_status.batch_worker_coverage",
        return_value={a.id: False},
    ):
        result = run_connection_test(db, a)
    assert result["success"] is False
    assert db.query(ChannelSession).filter(ChannelSession.account_id == a.id).count() == 0
    assert db.query(RubikaLoginChallenge).filter(RubikaLoginChallenge.account_id == a.id).count() == 0


def test_17_connection_test_manual_review_no_guess(db):
    a = _acct(db, phone="989140000211")
    _rubika_session(db, a, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="a")
    _rubika_session(db, a, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED, guid="b")
    with patch(
        "core_engine.services.account_runtime_status.batch_worker_coverage",
        return_value={a.id: True},
    ):
        result = run_connection_test(db, a)
    assert result["success"] is False
    assert result["status"] == RuntimeStatus.MANUAL_REVIEW.value


def test_18_worker_heartbeat_stale_changes_readiness(db):
    a = _acct(
        db,
        phone="989140000074",
        rubika_guid="guid-74",
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
    )
    _rubika_session(db, a, guid="guid-74")
    with patch(
        "core_engine.services.rubika_canonical_session.load_canonical_rubika_session",
        return_value=object(),
    ):
        ready = compute_account_runtime_status(
            db, a, worker_covered=True, dispatch_eligible_ids={a.id}
        )
        stale = compute_account_runtime_status(
            db, a, worker_covered=False, dispatch_eligible_ids={a.id}
        )
    assert ready.runtime_status == RuntimeStatus.READY.value
    assert stale.runtime_status == RuntimeStatus.AUTHENTICATED_NO_WORKER.value


def test_19_20_all_labels_and_no_enabled_lie():
    from core_engine.services.account_runtime_status import RUNTIME_STATUS_LABEL_FA

    for s in RuntimeStatus:
        assert s.value in RUNTIME_STATUS_LABEL_FA
        assert RUNTIME_STATUS_LABEL_FA[s.value] != "فعال" or s == RuntimeStatus.READY
    # READY label is آماده ارسال, never merely فعال
    assert RUNTIME_STATUS_LABEL_FA[RuntimeStatus.READY.value] == "آماده ارسال"


def test_24_bale_token_missing_login_required(db):
    a = _acct(db, platform=PlatformType.BALE, phone="989122222222")
    r = compute_account_runtime_status(db, a)
    assert r.runtime_status == RuntimeStatus.LOGIN_REQUIRED.value


def test_24b_telegram_adapter(db):
    a = _acct(db, platform=PlatformType.TELEGRAM, phone="@l18bot")
    r = compute_account_runtime_status(db, a)
    assert r.runtime_status == RuntimeStatus.LOGIN_REQUIRED.value
    assert r.credential_type == "api_token"


def test_25_26_no_unknown_status_or_reason():
    from core_engine.services.account_runtime_status import OPERATOR_ACTION_LABEL_FA

    for code in OPERATOR_ACTION_LABEL_FA:
        assert OPERATOR_ACTION_LABEL_FA[code]
