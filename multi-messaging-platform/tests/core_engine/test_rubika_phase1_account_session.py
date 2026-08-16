"""Rubika Phase 1 — account/session hardening tests.

Pins RUBIKA_DELIVERY_MODE explicitly. No live Rubika calls. Isolates Redis.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, ChannelSession, PlatformType, SessionType
from core_engine.services.account_session_wiring import (
    ACCOUNT_DISABLED,
    ACCOUNT_REQUIRES_LOGIN,
    CONFIG_INVALID,
    READY,
    SESSION_INVALID,
    SESSION_MISSING,
    USER_ACCOUNT_DISABLED,
    build_account_session_status,
    evaluate_account_session_readiness,
    register_api_token_session,
    required_session_type,
)
from core_engine.services.rubika_mode import (
    normalize_rubika_delivery_mode,
    rubika_required_session_type,
)
from core_engine.services.rubika_user_session import (
    RubikaLoginError,
    build_session_envelope,
    parse_session_envelope,
    start_rubika_user_login,
    verify_rubika_user_login,
)
from core_engine.services.session_storage import (
    load_channel_session_plaintext,
    store_channel_session,
)
from workers.rubika_account_pool import RubikaAccountPoolManager


@pytest.fixture(autouse=True)
def session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    from core_engine.services.redis_client import reset_redis_client

    reset_redis_client()
    yield
    reset_redis_client()


def _pin_mode(monkeypatch, mode: str, *, user_enabled: bool = False):
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", mode)
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true" if user_enabled else "false")
    get_settings.cache_clear()


def _make_rubika_account(session, *, status=AccountStatus.ACTIVE, phone="989120001111", label="p1"):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=phone,
        label=label,
        status=status,
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _cleanup_account(session, account_id: int):
    session.query(ChannelSession).filter(ChannelSession.account_id == account_id).delete()
    session.query(Account).filter(Account.id == account_id).delete()
    session.commit()


# --------------------------------------------------------------------------
# A. required_session_type / mode contract
# --------------------------------------------------------------------------


def test_normalize_rubika_delivery_mode_valid():
    assert normalize_rubika_delivery_mode(" Bot_API ") == "bot_api"
    assert normalize_rubika_delivery_mode("USER_ACCOUNT") == "user_account"


def test_normalize_rubika_delivery_mode_invalid():
    with pytest.raises(ValueError, match="bot_api"):
        normalize_rubika_delivery_mode("legacy_mtproto")


def test_required_session_type_bot_api_mapping():
    assert (
        required_session_type(PlatformType.RUBIKA, rubika_delivery_mode="bot_api")
        == SessionType.API_TOKEN
    )
    assert rubika_required_session_type("bot_api") == SessionType.API_TOKEN


def test_required_session_type_user_account_mapping():
    assert (
        required_session_type(PlatformType.RUBIKA, rubika_delivery_mode="user_account")
        == SessionType.RUBIKA_SESSION
    )
    assert rubika_required_session_type("user_account") == SessionType.RUBIKA_SESSION


def test_required_session_type_invalid_mode_raises():
    with pytest.raises(ValueError, match="bot_api"):
        required_session_type(PlatformType.RUBIKA, rubika_delivery_mode="not_a_mode")


def test_required_session_type_default_config_path(monkeypatch):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)
    assert required_session_type(PlatformType.RUBIKA) == SessionType.RUBIKA_SESSION
    _pin_mode(monkeypatch, "bot_api", user_enabled=False)
    assert required_session_type(PlatformType.RUBIKA) == SessionType.API_TOKEN


# --------------------------------------------------------------------------
# B / C. bot_api validation + user envelope
# --------------------------------------------------------------------------


def test_bot_api_register_valid_empty_malformed(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_rubika_account(session, status=AccountStatus.REQUIRES_LOGIN, label="bot-reg")

    with pytest.raises(ValueError, match="empty"):
        register_api_token_session(session, account=account, session_payload="   ")

    with pytest.raises(ValueError, match="invalid"):
        register_api_token_session(session, account=account, session_payload="{not-json")

    with pytest.raises(ValueError, match="bot_token"):
        register_api_token_session(
            session, account=account, session_payload=json.dumps({"foo": "bar"})
        )

    row = register_api_token_session(
        session, account=account, session_payload="  RUBIKA_BOT_TOKEN  "
    )
    session.commit()
    session.refresh(account)
    assert row.session_type == SessionType.API_TOKEN
    assert account.status == AccountStatus.ACTIVE
    plaintext = load_channel_session_plaintext(row)
    assert plaintext.decode() == "RUBIKA_BOT_TOKEN"

    _cleanup_account(session, account.id)
    session.close()


def test_bot_api_register_rejected_in_user_account_mode(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_rubika_account(session, label="bot-wrong-mode")
    with pytest.raises(ValueError, match="bot_api"):
        register_api_token_session(session, account=account, session_payload="TOKEN")
    _cleanup_account(session, account.id)
    session.close()


def test_user_envelope_version_roundtrip_and_rejects():
    envelope = build_session_envelope(
        phone_number="98912",
        auth="a" * 32,
        guid="u1",
        user_agent="ua",
        private_key="pk",
    )
    data = json.loads(envelope)
    assert data["version"] == 1
    parsed = parse_session_envelope(envelope)
    assert parsed["guid"] == "u1"
    assert "version" not in parsed

    # Legacy envelope without version still accepted as v1.
    legacy = json.dumps(
        {
            "phone_number": "98912",
            "auth": "a" * 32,
            "guid": "u2",
            "user_agent": "ua",
            "private_key": "pk",
        }
    )
    assert parse_session_envelope(legacy)["guid"] == "u2"

    with pytest.raises(ValueError, match="invalid"):
        parse_session_envelope("{bad")
    with pytest.raises(ValueError, match="missing"):
        parse_session_envelope(json.dumps({"phone_number": "x", "version": 1}))
    with pytest.raises(ValueError, match="Unsupported"):
        parse_session_envelope(
            json.dumps(
                {
                    "version": 99,
                    "phone_number": "98912",
                    "auth": "a" * 32,
                    "guid": "u",
                    "user_agent": "ua",
                    "private_key": "pk",
                }
            )
        )


# --------------------------------------------------------------------------
# D / E / J. persistence, restart, duplicate latest-row
# --------------------------------------------------------------------------


def test_bot_api_persistence_roundtrip_and_restart(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_rubika_account(session, label="bot-restart")
    register_api_token_session(session, account=account, session_payload="TOKEN_V1")
    session.commit()
    account_id = account.id
    session.close()

    # Simulate restart: clear settings cache, new DB session.
    get_settings.cache_clear()
    session2 = pg_session_factory()
    account2 = session2.get(Account, account_id)
    readiness = evaluate_account_session_readiness(session2, account2)
    assert readiness.ready is True
    assert readiness.code == READY
    assert readiness.delivery_mode == "bot_api"
    assert readiness.session_type == SessionType.API_TOKEN.value
    _cleanup_account(session2, account_id)
    session2.close()


def test_user_account_persistence_roundtrip_and_restart(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_rubika_account(session, label="user-restart")
    envelope = build_session_envelope(
        phone_number=account.phone_number,
        auth="c" * 32,
        guid="uRESTART",
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----",
    )
    store_channel_session(
        session,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=envelope,
    )
    session.commit()
    account_id = account.id
    session.close()

    get_settings.cache_clear()
    session2 = pg_session_factory()
    account2 = session2.get(Account, account_id)
    readiness = evaluate_account_session_readiness(session2, account2)
    assert readiness.ready is True
    assert readiness.delivery_mode == "user_account"
    assert readiness.session_type == SessionType.RUBIKA_SESSION.value
    status = build_account_session_status(session2, account2)
    assert status["delivery_mode"] == "user_account"
    assert status["ready_for_delivery"] is True
    _cleanup_account(session2, account_id)
    session2.close()


def test_duplicate_session_latest_row_wins(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_rubika_account(session, label="dup-sess")
    store_channel_session(
        session, account_id=account.id, session_type=SessionType.API_TOKEN, plaintext="OLD"
    )
    store_channel_session(
        session, account_id=account.id, session_type=SessionType.API_TOKEN, plaintext="NEW"
    )
    session.commit()
    readiness = evaluate_account_session_readiness(session, account)
    assert readiness.ready is True
    from workers.session_access import load_account_session_plaintext

    plain = load_account_session_plaintext(
        session, account_id=account.id, session_type=SessionType.API_TOKEN
    )
    assert plain.decode() == "NEW"
    rows = (
        session.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account.id,
            ChannelSession.session_type == SessionType.API_TOKEN,
        )
        .all()
    )
    assert len(rows) == 2
    _cleanup_account(session, account.id)
    session.close()


# --------------------------------------------------------------------------
# F / L. readiness matrix + config isolation
# --------------------------------------------------------------------------


def test_readiness_session_missing(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_rubika_account(session, label="ready-miss")
    readiness = evaluate_account_session_readiness(session, account)
    assert readiness.ready is False
    assert readiness.code == SESSION_MISSING
    assert readiness.error == "session_missing"
    _cleanup_account(session, account.id)
    session.close()


def test_readiness_requires_login_and_resting(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_rubika_account(
        session, status=AccountStatus.REQUIRES_LOGIN, label="ready-login"
    )
    store_channel_session(
        session, account_id=account.id, session_type=SessionType.API_TOKEN, plaintext="T"
    )
    session.commit()
    r1 = evaluate_account_session_readiness(session, account)
    assert r1.code == ACCOUNT_REQUIRES_LOGIN

    account.status = AccountStatus.RESTING
    session.commit()
    r2 = evaluate_account_session_readiness(session, account)
    assert r2.code == ACCOUNT_DISABLED
    assert r2.ready is False
    _cleanup_account(session, account.id)
    session.close()


def test_readiness_user_account_disabled(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=False)
    session = pg_session_factory()
    account = _make_rubika_account(session, label="ua-disabled")
    readiness = evaluate_account_session_readiness(session, account)
    assert readiness.ready is False
    assert readiness.code == USER_ACCOUNT_DISABLED
    _cleanup_account(session, account.id)
    session.close()


def test_readiness_invalid_envelope(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_rubika_account(session, label="bad-env")
    store_channel_session(
        session,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=json.dumps({"phone_number": "x"}),
    )
    session.commit()
    readiness = evaluate_account_session_readiness(session, account)
    assert readiness.ready is False
    assert readiness.code == SESSION_INVALID
    _cleanup_account(session, account.id)
    session.close()


def test_readiness_config_invalid_override(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "bot_api")
    session = pg_session_factory()
    account = _make_rubika_account(session, label="cfg-inv")
    readiness = evaluate_account_session_readiness(
        session, account, rubika_delivery_mode="nope"
    )
    assert readiness.code == CONFIG_INVALID
    _cleanup_account(session, account.id)
    session.close()


# --------------------------------------------------------------------------
# G / H / I. OTP flow
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_expired_token(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_rubika_account(session, status=AccountStatus.REQUIRES_LOGIN, label="otp-exp")
    with pytest.raises(RubikaLoginError, match="منقضی"):
        await verify_rubika_user_login(
            session, registration_token="missing-token-xyz", phone_code="12345"
        )
    session.refresh(account)
    assert account.status == AccountStatus.REQUIRES_LOGIN
    _cleanup_account(session, account.id)
    session.close()


@pytest.mark.asyncio
async def test_otp_wrong_code_does_not_corrupt(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_rubika_account(session, status=AccountStatus.REQUIRES_LOGIN, label="otp-wrong")

    from rubpy.types import Update

    async def fake_connect(self):
        return None

    async def fake_disconnect(self):
        return None

    async def mock_send_code(self, **kwargs):
        return Update({"status": "OK", "phone_code_hash": "hash123"})

    monkeypatch.setattr("rubpy.Client.connect", fake_connect)
    monkeypatch.setattr("rubpy.Client.disconnect", fake_disconnect)
    monkeypatch.setattr("rubpy.Client.send_code", mock_send_code)

    start = await start_rubika_user_login(account_id=account.id, phone_number="09121112233")
    token = start["registration_token"]

    async def fake_sign_in(self, **kwargs):
        return Update({"status": "ERROR", "auth": "", "user": {}})

    monkeypatch.setattr("rubpy.Client.sign_in", fake_sign_in)

    with pytest.raises(RubikaLoginError, match="ناموفق"):
        await verify_rubika_user_login(session, registration_token=token, phone_code="00000")

    session.refresh(account)
    assert account.status == AccountStatus.REQUIRES_LOGIN
    assert (
        session.query(ChannelSession)
        .filter(ChannelSession.account_id == account.id)
        .count()
        == 0
    )

    # Token still usable (not consumed on failure).
    from core_engine.services.redis_client import get_redis_client

    redis = get_redis_client()
    assert await redis.get(f"rubika:user_login:{token}") is not None

    _cleanup_account(session, account.id)
    session.close()


@pytest.mark.asyncio
async def test_otp_duplicate_verify_rejected(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_rubika_account(session, status=AccountStatus.REQUIRES_LOGIN, label="otp-dup")

    import base64

    from Crypto.Cipher import PKCS1_OAEP
    from Crypto.PublicKey import RSA
    from rubpy.types import Update

    async def fake_connect(self):
        return None

    async def fake_disconnect(self):
        return None

    async def mock_send_code(self, **kwargs):
        return Update({"status": "OK", "phone_code_hash": "hashDUP"})

    monkeypatch.setattr("rubpy.Client.connect", fake_connect)
    monkeypatch.setattr("rubpy.Client.disconnect", fake_disconnect)
    monkeypatch.setattr("rubpy.Client.send_code", mock_send_code)

    start = await start_rubika_user_login(account_id=account.id, phone_number="09123334455")
    token = start["registration_token"]

    from core_engine.services.redis_client import get_redis_client
    from rubpy.crypto import Crypto as RubikaCrypto

    redis = get_redis_client()
    state = json.loads(await redis.get(f"rubika:user_login:{token}"))
    public_key = state["public_key"]
    raw_pub_b64 = RubikaCrypto.decode_auth(public_key)
    pub_key_obj = RSA.import_key(base64.b64decode(raw_pub_b64))
    encrypted_auth = base64.b64encode(
        PKCS1_OAEP.new(pub_key_obj).encrypt(b"d" * 32)
    ).decode()

    async def fake_sign_in(self, **kwargs):
        return Update(
            {
                "status": "OK",
                "auth": encrypted_auth,
                "user": {"user_guid": "uDUP", "phone": "989123334455"},
            }
        )

    async def fake_register_device(self, **kwargs):
        return None

    monkeypatch.setattr("rubpy.Client.sign_in", fake_sign_in)
    monkeypatch.setattr("rubpy.Client.register_device", fake_register_device)

    first = await verify_rubika_user_login(session, registration_token=token, phone_code="11111")
    assert first["success"] is True
    session.commit()

    with pytest.raises(RubikaLoginError, match="منقضی"):
        await verify_rubika_user_login(session, registration_token=token, phone_code="11111")

    count = (
        session.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account.id,
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
        )
        .count()
    )
    assert count == 1
    _cleanup_account(session, account.id)
    session.close()


@pytest.mark.asyncio
async def test_otp_redis_failure_clear_error(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)

    class BoomRedis:
        async def get(self, *_a, **_k):
            raise ConnectionError("redis down")

        async def set(self, *_a, **_k):
            raise ConnectionError("redis down")

        async def delete(self, *_a, **_k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(
        "core_engine.services.rubika_user_session.get_redis_client",
        lambda: BoomRedis(),
    )
    with pytest.raises(RubikaLoginError, match="Redis"):
        await start_rubika_user_login(account_id=1, phone_number="09120000000")


# --------------------------------------------------------------------------
# K. account status transitions
# --------------------------------------------------------------------------


def test_account_status_transitions_pool(monkeypatch, pg_session_factory):
    _pin_mode(monkeypatch, "user_account", user_enabled=True)
    session = pg_session_factory()
    account = _make_rubika_account(session, label="trans")
    pool = RubikaAccountPoolManager(session)

    pool.mark_account_failed(account_id=account.id, error_message="temp", permanent=False)
    session.refresh(account)
    assert account.status == AccountStatus.RESTING

    pool.mark_account_restored(account_id=account.id)
    session.refresh(account)
    assert account.status == AccountStatus.ACTIVE

    pool.mark_account_failed(
        account_id=account.id, error_message="auth", requires_relogin=True
    )
    session.refresh(account)
    assert account.status == AccountStatus.REQUIRES_LOGIN

    pool.mark_account_restored(account_id=account.id)
    session.refresh(account)
    assert account.status == AccountStatus.ACTIVE

    pool.mark_account_failed(account_id=account.id, error_message="ban", permanent=True)
    session.refresh(account)
    assert account.status == AccountStatus.BANNED
    pool.mark_account_restored(account_id=account.id)
    session.refresh(account)
    assert account.status == AccountStatus.BANNED  # banned not auto-restored

    _cleanup_account(session, account.id)
    session.close()
