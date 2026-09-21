"""ACTIVE Rubika accounts: canonical session vs L3 re-login check."""

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
from core_engine.services.rubika_canonical_login_audit import (
    CANONICAL_OK,
    NO_ACTIVE_SESSION,
    NOT_CANONICAL_MANAGED,
    classify_active_rubika_account,
    refresh_canonical_session_checks,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_active_unbound_session_needs_relogin(db):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number="989120008803",
        label="audit-unbound",
        status=AccountStatus.ACTIVE,
        rubika_identity_status=RubikaIdentityStatus.UNBOUND,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    env = build_session_envelope(
        phone_number=account.phone_number,
        auth="b" * 32,
        guid="guid-unbound",
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nold\n-----END RSA PRIVATE KEY-----",
    )
    store_channel_session(
        db,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=env,
        session_status=RubikaSessionStatus.ACTIVE,
        identity_guid="guid-unbound",
    )
    db.commit()
    result = classify_active_rubika_account(db, account)
    assert result.has_canonical_session is False
    assert result.needs_relogin is True
    assert result.reason == NOT_CANONICAL_MANAGED


def test_active_without_session_needs_relogin(db):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number="989120008801",
        label="audit-none",
        status=AccountStatus.ACTIVE,
        rubika_identity_status=RubikaIdentityStatus.UNBOUND,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    result = classify_active_rubika_account(db, account)
    assert result.has_canonical_session is False
    assert result.needs_relogin is True
    assert result.reason == NO_ACTIVE_SESSION


def test_canonical_active_does_not_need_relogin(db):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number="989120008802",
        label="audit-ok",
        status=AccountStatus.ACTIVE,
        rubika_guid="guid-ok",
        rubika_identity_status=RubikaIdentityStatus.VERIFIED,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    env = build_session_envelope(
        phone_number=account.phone_number,
        auth="a" * 32,
        guid="guid-ok",
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nold\n-----END RSA PRIVATE KEY-----",
    )
    store_channel_session(
        db,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=env,
        session_status=RubikaSessionStatus.ACTIVE,
        identity_guid="guid-ok",
    )
    db.commit()
    result = classify_active_rubika_account(db, account)
    assert result.has_canonical_session is True
    assert result.needs_relogin is False
    assert result.reason == CANONICAL_OK
    n = refresh_canonical_session_checks(db)
    assert n >= 1
