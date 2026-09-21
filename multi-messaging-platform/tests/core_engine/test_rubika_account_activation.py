"""Rubika manager activation gate — login ≠ send-ready."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    PlatformType,
    RubikaAccountActivation,
    RubikaAccountActivationState,
    RubikaAccountPool,
    RubikaIdentityStatus,
    RubikaSenderSchedule,
)
from core_engine.services.rubika_account_activation import (
    ACTIVATION_PENDING,
    READY_TO_SEND,
    RUBIKA_ACTIVATION_PENDING,
    confirm_activation,
    send_activation_state,
    start_activation_after_login,
    try_confirm_from_inbound_text,
)
from core_engine.services.rubika_login_fake_provider import (
    FakeRubikaLoginProvider,
    PassThroughCandidateProver,
)
from core_engine.services.rubika_login_state_machine import (
    request_rubika_login,
    submit_rubika_login_code,
)
from core_engine.services.rubika_user_session import RubikaLoginError, start_rubika_user_login


class RecordingActivationSender:
    def __init__(self):
        self.calls: list[dict] = []

    async def send(self, *, account_id: int, manager_phone: str, text: str) -> str | None:
        self.calls.append(
            {"account_id": account_id, "manager_phone": manager_phone, "text": text}
        )
        return "activation-msg-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "true")
    monkeypatch.setenv("RUBIKA_MANAGER_ACTIVATION_REQUIRED", "true")
    monkeypatch.setenv("RUBIKA_ACTIVATION_MANAGER_PHONE", "09903858654")
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    monkeypatch.setenv("RUBIKA_OTP_RESEND_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("OPS_LIVE_SEND_API_ENABLED", "true")
    monkeypatch.setenv("REAL_MESSAGE_SENDING_ENABLED", "true")
    monkeypatch.setenv("CHANNEL_CONNECTORS_ENABLED", "true")
    monkeypatch.setenv("DRY_RUN", "false")
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


def _acct(db, phone="989120009001"):
    a = Account(
        platform=PlatformType.RUBIKA,
        phone_number=phone,
        label="activation",
        status=AccountStatus.REQUIRES_LOGIN,
        rubika_identity_status=RubikaIdentityStatus.UNBOUND,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _phase_day(db):
    existing = (
        db.query(RubikaSenderSchedule)
        .filter_by(phase="day", slot=1)
        .first()
    )
    if existing is not None:
        existing.is_active = True
        existing.start_hour = 0
        existing.end_hour = 23
        db.commit()
        return existing
    row = RubikaSenderSchedule(phase="day", slot=1, start_hour=0, end_hour=23, is_active=True)
    db.add(row)
    db.commit()
    return row


async def _login(db, account, sender=None):
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    req = await request_rubika_login(
        db, account.id, phone_number=account.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    return await submit_rubika_login_code(
        db,
        account.id,
        req.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
        activation_sender=sender if sender is not None else RecordingActivationSender(),
    )


@pytest.mark.asyncio
async def test_login_does_not_enroll_send_pool(db):
    _phase_day(db)
    account = _acct(db)
    sender = RecordingActivationSender()
    result = await _login(db, account, sender=sender)
    db.commit()
    assert result.ok is True
    assert result.dispatch_block == RUBIKA_ACTIVATION_PENDING
    assert db.query(RubikaAccountPool).filter_by(account_id=account.id).count() == 0
    pending = (
        db.query(RubikaAccountActivation)
        .filter_by(account_id=account.id)
        .one()
    )
    assert pending.state == RubikaAccountActivationState.CHALLENGE_SENT
    assert pending.challenge_message_id == "activation-msg-1"
    assert send_activation_state(db, account.id) == ACTIVATION_PENDING


@pytest.mark.asyncio
async def test_activation_challenge_message_is_sent(db):
    account = _acct(db, phone="989120009011")
    sender = RecordingActivationSender()
    await _login(db, account, sender=sender)
    db.commit()
    assert len(sender.calls) == 1
    call = sender.calls[0]
    assert call["manager_phone"] == "09903858654"
    assert call["account_id"] == account.id
    assert "اکانت روبیکا آماده تایید است." in call["text"]
    assert "989120009011" in call["text"]
    assert "برای فعال‌سازی ارسال تایید کنید." in call["text"]
    row = db.query(RubikaAccountActivation).filter_by(account_id=account.id).one()
    assert row.state == RubikaAccountActivationState.CHALLENGE_SENT
    assert row.challenge_message_id == "activation-msg-1"


@pytest.mark.asyncio
async def test_manager_confirm_enrolls_pool_and_is_ready_to_send(db):
    _phase_day(db)
    account = _acct(db)
    await _login(db, account)
    db.commit()
    confirmed = confirm_activation(db, account.id, actor="admin")
    db.commit()
    assert confirmed.ok is True
    assert confirmed.send_activation_state == READY_TO_SEND
    row = (
        db.query(RubikaAccountActivation)
        .filter_by(account_id=account.id)
        .one()
    )
    assert row.state == RubikaAccountActivationState.CONFIRMED
    assert db.query(RubikaAccountPool).filter_by(account_id=account.id).count() == 1
    assert send_activation_state(db, account.id) == READY_TO_SEND
    from core_engine.services.operational_send import build_live_send_preflight
    from core_engine.services.rubika_account_activation import assert_send_activation_allowed

    assert assert_send_activation_allowed(db, account.id, context="worker") is None
    live = build_live_send_preflight(db, account)
    activation_check = next(
        item for item in live["checks"] if item["key"] == "rubika_activation_confirmed"
    )
    assert activation_check["passed"] is True
    assert live["ready_for_live_send"] is True


@pytest.mark.asyncio
async def test_inbound_confirm_code_matches(db):
    account = _acct(db, phone="989120009002")
    start_activation_after_login(db, account.id)
    db.commit()
    row = (
        db.query(RubikaAccountActivation)
        .filter_by(account_id=account.id)
        .one()
    )
    matched = try_confirm_from_inbound_text(
        db, account_id=account.id, text=f"ok {row.confirm_code} thanks"
    )
    db.commit()
    assert matched is not None
    assert matched.ok is True
    assert matched.send_activation_state == READY_TO_SEND


@pytest.mark.asyncio
async def test_campaign_preflight_denies_pending_activation(db):
    account = _acct(db, phone="989120009003")
    await _login(db, account)
    db.commit()
    from core_engine.services.rubika_account_activation import assert_send_activation_allowed

    assert assert_send_activation_allowed(db, account.id, context="worker") == RUBIKA_ACTIVATION_PENDING
    assert assert_send_activation_allowed(db, account.id, context="activation") is None
    confirm_activation(db, account.id, actor="admin")
    db.commit()
    assert assert_send_activation_allowed(db, account.id, context="worker") is None


def test_account_without_activation_row_cannot_send_when_required(db):
    account = _acct(db, phone="989120009004")
    account.status = AccountStatus.ACTIVE
    db.commit()
    from core_engine.services.operational_send import build_live_send_preflight
    from core_engine.services.rubika_account_activation import assert_send_activation_allowed

    assert send_activation_state(db, account.id) == ACTIVATION_PENDING
    assert assert_send_activation_allowed(db, account.id, context="worker") == RUBIKA_ACTIVATION_PENDING
    assert assert_send_activation_allowed(db, account.id, context="activation") is None
    preflight = build_live_send_preflight(db, account)
    activation_check = next(
        item for item in preflight["checks"] if item["key"] == "rubika_activation_confirmed"
    )
    assert activation_check["passed"] is False
    assert RUBIKA_ACTIVATION_PENDING in activation_check["message"]
    assert preflight["ready_for_live_send"] is False


def test_flag_off_allows_send_without_activation_row(db, monkeypatch):
    monkeypatch.setenv("RUBIKA_MANAGER_ACTIVATION_REQUIRED", "false")
    get_settings.cache_clear()
    account = _acct(db, phone="989120009005")
    account.status = AccountStatus.ACTIVE
    db.commit()
    from core_engine.services.rubika_account_activation import (
        NOT_REQUIRED,
        assert_send_activation_allowed,
    )

    assert send_activation_state(db, account.id) == NOT_REQUIRED
    assert assert_send_activation_allowed(db, account.id, context="worker") is None


def test_preflight_worker_error_code_is_rubika_activation_pending():
    from core_engine.services.rubika_preflight import (
        RUBIKA_ACTIVATION_PENDING as PREFLIGHT_DENY,
        RubikaPreflightResult,
        preflight_to_worker_result,
    )

    denied = RubikaPreflightResult(
        allowed=False,
        code=PREFLIGHT_DENY,
        message="ورود موفق است؛ ارسال کمپین و send-test زنده تا تایید مدیر مجاز نیست.",
    )
    result = preflight_to_worker_result(denied)
    assert result.error_code == RUBIKA_ACTIVATION_PENDING
    assert result.success is False
    assert result.retryable is False


@pytest.mark.asyncio
async def test_new_account_legacy_login_is_blocked(db):
    _phase_day(db)
    account = _acct(db, phone="989120009020")
    with pytest.raises(RubikaLoginError, match="LEGACY_LOGIN"):
        await start_rubika_user_login(
            account_id=account.id, phone_number=account.phone_number
        )
    sender = RecordingActivationSender()
    result = await _login(db, account, sender=sender)
    db.commit()
    assert result.ok is True
    row = db.query(RubikaAccountActivation).filter_by(account_id=account.id).one()
    assert row.state == RubikaAccountActivationState.CHALLENGE_SENT
    assert db.query(RubikaAccountPool).filter_by(account_id=account.id).count() == 0
    confirmed = confirm_activation(db, account.id, actor="admin")
    db.commit()
    assert confirmed.send_activation_state == READY_TO_SEND
    assert db.query(RubikaAccountPool).filter_by(account_id=account.id).count() == 1


def test_mask_phone_hides_middle_digits():
    from core_engine.services.rubika_account_activation import _mask_phone

    assert _mask_phone("989913252068") == "****2068"
    assert "9913" not in _mask_phone("989913252068")


@pytest.mark.asyncio
async def test_activation_sender_connects_before_add_address_book(monkeypatch):
    from types import SimpleNamespace

    from core_engine.services.rubika_account_activation import RubikaUserActivationSender

    order: list[str] = []

    class FakeClient:
        async def add_address_book(self, **kwargs):
            order.append("add_address_book")
            return {
                "user": {"user_guid": "uMANAGER"},
                "user_exist": True,
            }

        async def send_message(self, **kwargs):
            order.append("send_message")
            return {"message_id": "live-msg-9"}

        async def disconnect(self):
            order.append("disconnect")

    async def fake_load(account_id, db=None):
        order.append("load")
        return FakeClient()

    async def fake_connect(client):
        order.append("connect")

    async def fake_preflight(*args, **kwargs):
        return SimpleNamespace(allowed=True, code="ok")

    class FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(
        "core_engine.database.SessionLocal",
        lambda: FakeSession(),
    )
    monkeypatch.setattr(
        "core_engine.services.rubika_preflight.require_rubika_side_channel_send",
        fake_preflight,
    )
    monkeypatch.setattr(
        "workers.connectors.rubika_user.load_rubika_user_client",
        fake_load,
    )
    monkeypatch.setattr(
        "workers.connectors.rubika_user._connect_authenticated",
        fake_connect,
    )
    message_id = await RubikaUserActivationSender().send(
        account_id=1,
        manager_phone="09913252068",
        text="test",
    )
    assert message_id == "live-msg-9"
    assert order[:4] == ["load", "connect", "add_address_book", "send_message"]
    assert "disconnect" in order


def test_activation_sender_source_uses_connector_connect():
    from pathlib import Path

    import core_engine.services.rubika_account_activation as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from workers.connectors.rubika_user import" in src
    assert "_connect_authenticated" in src
    assert "await _connect_authenticated(client)" in src
    assert "await client.add_address_book" in src
    assert "await client.send_message" in src
    assert "_mask_phone(phone)" in src
    assert "event=rubika_activation_send_result" in src
    assert "result=%r" not in src
