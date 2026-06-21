import json

import httpx
import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, PlatformType, SessionType
from core_engine.services.session_storage import store_channel_session
from workers.config import WorkerSettings
from workers.connectors import bale as bale_connector
from workers.connectors.bale import (
    deliver_bale_live,
    parse_bale_bot_token,
    resolve_bale_chat_id,
    send_bale_text_message,
)
from workers.delivery import deliver_platform_message
from workers.errors import PermanentWorkerError, SessionInvalidError
from workers.payloads import WorkerPayload, WorkerResult


def _live_settings() -> WorkerSettings:
    return WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        BALE_API_BASE_URL="https://tapi.bale.ai",
        BALE_API_TIMEOUT_SECONDS=5,
    )


def _sample_payload(**overrides) -> WorkerPayload:
    base = dict(
        message_id=1,
        campaign_id=10,
        contact_id=20,
        account_id=1,
        platform="bale",
        recipient="123456789",
        recipient_type="channel_handle",
        message_text="سلام از بله",
        dedupe_key="dedupe-1",
    )
    base.update(overrides)
    return WorkerPayload.model_validate(base)


def test_parse_bale_bot_token_plain():
    assert parse_bale_bot_token(b"123456:ABC-DEF") == "123456:ABC-DEF"


def test_parse_bale_bot_token_json():
    payload = json.dumps({"bot_token": "999:TOKEN"}).encode("utf-8")
    assert parse_bale_bot_token(payload) == "999:TOKEN"


def test_parse_bale_bot_token_empty_raises():
    with pytest.raises(SessionInvalidError):
        parse_bale_bot_token(b"  ")


def test_resolve_bale_chat_id_from_channel_handle():
    payload = _sample_payload(recipient="987654321", recipient_type="channel_handle")
    assert resolve_bale_chat_id(payload) == 987654321


def test_resolve_bale_chat_id_from_metadata():
    payload = _sample_payload(
        recipient="+989121111111",
        recipient_type="phone_number",
        metadata={"chat_id": "555"},
    )
    assert resolve_bale_chat_id(payload) == 555


def test_resolve_bale_chat_id_phone_without_handle_fails():
    payload = _sample_payload(recipient="+989121111111", recipient_type="phone_number")
    with pytest.raises(PermanentWorkerError):
        resolve_bale_chat_id(payload)


@pytest.mark.asyncio
async def test_send_bale_text_message_success(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 42}}

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(bale_connector, "request_bale_api", fake_request)

    result = await send_bale_text_message(
        bot_token="1:TOKEN",
        chat_id=123,
        text="hello",
        settings=_live_settings(),
    )
    assert result.success is True
    assert result.status == "delivered"
    assert result.platform_message_id == "bale-42"


@pytest.mark.asyncio
async def test_send_bale_text_message_rate_limited(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": False, "error_code": 429, "description": "Too Many Requests"}

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(bale_connector, "request_bale_api", fake_request)

    result = await send_bale_text_message(
        bot_token="1:TOKEN",
        chat_id=123,
        text="hello",
        settings=_live_settings(),
    )
    assert result.success is False
    assert result.status == "failed_retryable"
    assert result.error_code == "bale_rate_limited"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_deliver_bale_live_missing_session():
    result = await deliver_bale_live(_sample_payload(), _live_settings())
    assert result.success is False
    assert result.error_code == "bale_session_missing"


@pytest.mark.asyncio
async def test_deliver_bale_live_success(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="bale-bot-1",
        label="Bale Bot",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()

    store_channel_session(
        session,
        account_id=account.id,
        session_type=SessionType.API_TOKEN,
        plaintext="111:TESTTOKEN",
    )
    session.commit()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 7}}

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(bale_connector, "request_bale_api", fake_request)

    payload = _sample_payload(account_id=account.id, recipient="321", recipient_type="channel_handle")
    result = await deliver_bale_live(payload, _live_settings())
    assert result.success is True
    assert result.status == "delivered"
    session.close()


@pytest.mark.asyncio
async def test_deliver_platform_message_routes_to_bale_live(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="bale-bot-2",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()
    store_channel_session(
        session,
        account_id=account.id,
        session_type=SessionType.API_TOKEN,
        plaintext="222:TOKEN",
    )
    session.commit()

    async def fake_deliver(payload, settings):
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="bale-99",
        )

    monkeypatch.setattr("workers.delivery.deliver_bale_live", fake_deliver)

    payload = _sample_payload(account_id=account.id)
    result = await deliver_platform_message("bale", payload, _live_settings())
    assert result.success is True
    assert result.platform_message_id == "bale-99"
    session.close()


@pytest.fixture(autouse=True)
def worker_session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
