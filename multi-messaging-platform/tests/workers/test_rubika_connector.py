import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core_engine.config import get_settings
from core_engine.database import Base
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    ChannelSession,
    PlatformType,
    SessionType,
)
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
    # Pin the delivery mode: the bot_api path under test must not depend on
    # whatever RUBIKA_DELIVERY_MODE a developer's local .env happens to carry.
    return WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        RUBIKA_API_BASE_URL="https://botapi.rubika.ir/v3",
        RUBIKA_API_TIMEOUT_SECONDS=5,
        RUBIKA_DELIVERY_MODE="bot_api",
        RUBIKA_USER_ACCOUNT_ENABLED=False,
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
async def test_deliver_rubika_live_missing_session(
    monkeypatch,
    rubika_sqlite_session_factory,
):
    """Preflight blocks before transport when no session is registered."""
    SessionLocal, account_id = rubika_sqlite_session_factory
    transport_calls = {"count": 0}

    async def fake_request(*args, **kwargs):
        transport_calls["count"] += 1
        raise AssertionError("transport must not be called")

    monkeypatch.setattr(rubika_connector, "request_rubika_api", fake_request)

    result = await deliver_rubika_live(
        _sample_payload(account_id=account_id),
        _live_settings(),
    )

    assert result.success is False
    assert result.error_code == "rubika_session_missing"
    assert transport_calls["count"] == 0


@pytest.mark.asyncio
async def test_deliver_rubika_live_success(
    monkeypatch,
    rubika_sqlite_session_factory,
):
    """Exercise the real stored-token path while mocking only outbound HTTP."""
    SessionLocal, account_id = rubika_sqlite_session_factory

    token_session = SessionLocal()
    try:
        store_channel_session(
            token_session,
            account_id=account_id,
            session_type=SessionType.API_TOKEN,
            plaintext="RUBIKA_TEST_TOKEN",
        )
        token_session.commit()
    finally:
        token_session.close()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"message_id": "777"}

    seen = {}

    async def fake_request(
        method,
        url,
        *,
        json_body=None,
        timeout_seconds=None,
    ):
        seen["method"] = method
        seen["url"] = url
        seen["json_body"] = json_body
        seen["timeout_seconds"] = timeout_seconds
        return FakeResponse()

    monkeypatch.setattr(
        rubika_connector,
        "request_rubika_api",
        fake_request,
    )

    payload = _sample_payload(
        account_id=account_id,
        recipient="chat-target-1",
        recipient_type="channel_handle",
    )
    result = await deliver_rubika_live(payload, _live_settings())

    assert result.success is True
    assert result.platform_message_id == "rubika-777"
    assert seen["method"] == "POST"
    assert "RUBIKA_TEST_TOKEN" in seen["url"]
    assert seen["json_body"] == {
        "chat_id": "chat-target-1",
        "text": "سلام از روبیکا",
    }


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


@pytest.fixture
def rubika_sqlite_session_factory(worker_session_secret, monkeypatch):
    """Shared in-memory SQLite database holding one Rubika account.

    ``deliver_rubika_live`` loads the stored token through a fresh database
    session. ``StaticPool`` keeps all SQLite sessions on the same in-memory
    connection, allowing the real storage and decryption path to be tested
    without Postgres.
    """
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(
        engine,
        tables=[
            Account.__table__,
            ChannelSession.__table__,
            Campaign.__table__,
            CampaignAccount.__table__,
        ],
    )

    setup_session = SessionLocal()
    try:
        account = Account(
            platform=PlatformType.RUBIKA,
            phone_number="rubika-bot-1",
            label="Rubika Bot",
            status=AccountStatus.ACTIVE,
        )
        setup_session.add(account)
        setup_session.commit()
        setup_session.refresh(account)
        account_id = account.id
    finally:
        setup_session.close()

    monkeypatch.setattr(rubika_connector, "get_db_session", SessionLocal)

    try:
        yield SessionLocal, account_id
    finally:
        engine.dispose()
