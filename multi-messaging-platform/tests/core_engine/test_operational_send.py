"""Tests for operational send service (Phase 9.1+)."""

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, PlatformType
from core_engine.services.account_session_wiring import register_api_token_session
from core_engine.services.operational_send import (
    OperationalSendError,
    build_live_send_preflight,
    build_operational_worker_settings,
    build_test_worker_payload,
    operational_send_capabilities,
    send_account_test_message,
)
from workers.payloads import WorkerResult


@pytest.fixture(autouse=True)
def session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    from workers.config import get_worker_settings

    get_worker_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


def _enable_live_env(monkeypatch):
    monkeypatch.setenv("OPS_LIVE_SEND_API_ENABLED", "true")
    monkeypatch.setenv("REAL_MESSAGE_SENDING_ENABLED", "true")
    monkeypatch.setenv("CHANNEL_CONNECTORS_ENABLED", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    from workers.config import get_worker_settings

    get_worker_settings.cache_clear()


def test_build_test_worker_payload_whatsapp():
    account = Account(
        id=1,
        platform=PlatformType.WHATSAPP,
        phone_number="09121234567",
        status=AccountStatus.ACTIVE,
    )
    payload = build_test_worker_payload(account, message_text="سلام تست")
    assert payload.platform == "whatsapp"
    assert payload.recipient == "09121234567"
    assert payload.recipient_type == "phone_number"


def test_build_test_worker_payload_telegram_requires_handle():
    account = Account(
        id=2,
        platform=PlatformType.TELEGRAM,
        phone_number=None,
        status=AccountStatus.ACTIVE,
    )
    with pytest.raises(OperationalSendError):
        build_test_worker_payload(account, message_text="test")


def test_build_operational_worker_settings_dry_run_default():
    settings = build_operational_worker_settings(dry_run=True, confirm_live_send=False)
    assert settings.DRY_RUN is True
    assert settings.CHANNEL_CONNECTORS_ENABLED is False


def test_build_operational_worker_settings_live_requires_confirm():
    with pytest.raises(OperationalSendError):
        build_operational_worker_settings(dry_run=False, confirm_live_send=False)


def test_operational_send_capabilities():
    caps = operational_send_capabilities()
    assert caps["dry_run_default"] is True
    assert caps["ops_live_send_api_enabled"] is False
    assert caps["live_send_allowed"] is False


def test_build_live_send_preflight_requires_session(pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="chat-1",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    report = build_live_send_preflight(session, account)
    assert report["ready_for_live_send"] is False
    assert any(item["key"] == "session_ready" and not item["passed"] for item in report["checks"])
    session.close()


def test_build_operational_worker_settings_live_requires_ops_api_flag(monkeypatch):
    monkeypatch.setenv("REAL_MESSAGE_SENDING_ENABLED", "true")
    monkeypatch.setenv("CHANNEL_CONNECTORS_ENABLED", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("OPS_LIVE_SEND_API_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(OperationalSendError, match="OPS_LIVE_SEND_API_ENABLED"):
        build_operational_worker_settings(dry_run=False, confirm_live_send=True)


@pytest.mark.asyncio
async def test_send_account_test_message_live_mocked(monkeypatch, pg_session_factory):
    _enable_live_env(monkeypatch)
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="chat-live",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()
    register_api_token_session(session, account=account, session_payload="LIVE_TOKEN")
    session.commit()
    account_id = account.id

    async def fake_deliver(platform, payload, settings):
        assert platform == "bale"
        assert settings.DRY_RUN is False
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="live-msg-1",
            retryable=False,
        )

    monkeypatch.setattr(
        "workers.delivery.deliver_platform_message",
        fake_deliver,
    )

    account = session.get(Account, account_id)
    result = await send_account_test_message(
        session,
        account,
        message_text="live test",
        recipient="chat-live",
        dry_run=False,
        confirm_live_send=True,
    )
    assert result["success"] is True
    assert result["live_send"] is True
    assert result["status"] == "delivered"
    session.close()


@pytest.mark.asyncio
async def test_send_account_test_message_dry_run(pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="chat-123",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()
    register_api_token_session(session, account=account, session_payload="TOKEN")
    session.commit()
    account_id = account.id
    session.close()

    session = pg_session_factory()
    account = session.get(Account, account_id)
    result = await send_account_test_message(
        session,
        account,
        message_text="dry run test",
        recipient="chat-123",
        dry_run=True,
    )
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["status"] == "dry_run"
    session.close()
