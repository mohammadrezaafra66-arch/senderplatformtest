import json

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, PlatformType, SessionType
from core_engine.services.session_storage import store_channel_session
from workers.config import WorkerSettings
from workers.connectors import rubika as rubika_connector
from workers.connectors.rubika import (
    _result_from_rubika_response,
    deliver_rubika_live,
    parse_rubika_bot_token,
    resolve_rubika_chat_id,
    send_rubika_text_message,
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
        RUBIKA_API_BASE_URL="https://botapi.rubika.ir/v3",
        RUBIKA_API_TIMEOUT_SECONDS=5,
    )


def _sample_payload(**overrides) -> WorkerPayload:
    base = dict(
        message_id=1,
        campaign_id=10,
        contact_id=20,
        account_id=1,
        platform="rubika",
        recipient="b0QFtabc1I02214b529f1d60c9ce5b08",
        recipient_type="channel_handle",
        message_text="سلام از روبیکا",
        dedupe_key="dedupe-rubika-1",
    )
    base.update(overrides)
    return WorkerPayload.model_validate(base)


def test_parse_rubika_bot_token_plain():
    assert parse_rubika_bot_token(b"SUPER_SECRET_TOKEN") == "SUPER_SECRET_TOKEN"


def test_parse_rubika_bot_token_json():
    payload = json.dumps({"bot_token": "RUBIKA_TOKEN"}).encode("utf-8")
    assert parse_rubika_bot_token(payload) == "RUBIKA_TOKEN"


def test_parse_rubika_bot_token_empty_raises():
    with pytest.raises(SessionInvalidError):
        parse_rubika_bot_token(b"  ")


def test_resolve_rubika_chat_id_from_channel_handle():
    payload = _sample_payload(
        recipient="b0QFtabc1I02214b529f1d60c9ce5b08",
        recipient_type="channel_handle",
    )
    assert resolve_rubika_chat_id(payload) == "b0QFtabc1I02214b529f1d60c9ce5b08"


def test_resolve_rubika_chat_id_from_metadata():
    payload = _sample_payload(
        recipient="09121234567",
        recipient_type="phone_number",
        metadata={"chat_id": "chat-abc-123"},
    )
    assert resolve_rubika_chat_id(payload) == "chat-abc-123"


def test_resolve_rubika_chat_id_empty_fails():
    payload = _sample_payload(recipient="  ", recipient_type="channel_handle")
    with pytest.raises(PermanentWorkerError):
        resolve_rubika_chat_id(payload)


def test_result_from_rubika_response_success_flat():
    result = _result_from_rubika_response({"message_id": "204216801381244279"})
    assert result.success is True
    assert result.platform_message_id == "rubika-204216801381244279"


def test_result_from_rubika_response_success_wrapped():
    result = _result_from_rubika_response(
        {
            "status": "OK",
            "data": {"message_id": "msg-99"},
        }
    )
    assert result.success is True
    assert result.platform_message_id == "rubika-msg-99"


def test_result_from_rubika_response_error_retryable():
    result = _result_from_rubika_response(
        {
            "status": "Error",
            "code": 429,
            "dev_message": "Too many requests",
        }
    )
    assert result.success is False
    assert result.error_code == "rubika_rate_limited"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_send_rubika_text_message_success(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "OK", "data": {"message_id": "rubika-msg-1"}}

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(rubika_connector, "request_rubika_api", fake_request)

    result = await send_rubika_text_message(
        bot_token="TOKEN",
        chat_id="chat-1",
        text="hello",
        settings=_live_settings(),
    )
    assert result.success is True
    assert result.platform_message_id == "rubika-rubika-msg-1"


@pytest.mark.asyncio
async def test_deliver_rubika_live_missing_session():
    result = await deliver_rubika_live(_sample_payload(), _live_settings())
    assert result.success is False
    assert result.error_code == "rubika_session_missing"


@pytest.mark.asyncio
async def test_deliver_rubika_live_success(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number="rubika-bot-1",
        label="Rubika Bot",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()

    store_channel_session(
        session,
        account_id=account.id,
        session_type=SessionType.API_TOKEN,
        plaintext="RUBIKA_TEST_TOKEN",
    )
    session.commit()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"message_id": "777"}

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(rubika_connector, "request_rubika_api", fake_request)

    payload = _sample_payload(
        account_id=account.id,
        recipient="chat-target-1",
        recipient_type="channel_handle",
    )
    result = await deliver_rubika_live(payload, _live_settings())
    assert result.success is True
    assert result.platform_message_id == "rubika-777"
    session.close()


@pytest.mark.asyncio
async def test_deliver_platform_message_routes_to_rubika_live(monkeypatch):
    async def fake_deliver(payload, settings):
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="rubika.ROUTED",
        )

    monkeypatch.setattr("workers.delivery.deliver_rubika_live", fake_deliver)

    payload = _sample_payload()
    result = await deliver_platform_message("rubika", payload, _live_settings())
    assert result.success is True
    assert result.platform_message_id == "rubika.ROUTED"


@pytest.fixture(autouse=True)
def worker_session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
