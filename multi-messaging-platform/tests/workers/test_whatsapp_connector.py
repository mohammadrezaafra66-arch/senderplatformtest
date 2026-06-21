import json

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, PlatformType, SessionType
from core_engine.services.session_storage import store_channel_session
from workers.config import WorkerSettings
from workers.connectors import whatsapp as whatsapp_connector
from workers.connectors.whatsapp import (
    WhatsAppCredentials,
    deliver_whatsapp_live,
    parse_whatsapp_credentials,
    resolve_whatsapp_recipient,
    send_whatsapp_text_message,
    to_whatsapp_recipient_e164_digits,
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
        WHATSAPP_API_BASE_URL="https://graph.facebook.com/v21.0",
        WHATSAPP_API_TIMEOUT_SECONDS=5,
    )


def _sample_payload(**overrides) -> WorkerPayload:
    base = dict(
        message_id=1,
        campaign_id=10,
        contact_id=20,
        account_id=1,
        platform="whatsapp",
        recipient="09121234567",
        recipient_type="phone_number",
        message_text="سلام از واتساپ",
        dedupe_key="dedupe-1",
    )
    base.update(overrides)
    return WorkerPayload.model_validate(base)


def _valid_credentials_json() -> bytes:
    return json.dumps(
        {
            "access_token": "EAA_TEST_TOKEN",
            "phone_number_id": "123456789012345",
        }
    ).encode("utf-8")


def test_parse_whatsapp_credentials_success():
    creds = parse_whatsapp_credentials(_valid_credentials_json())
    assert creds == WhatsAppCredentials(
        access_token="EAA_TEST_TOKEN",
        phone_number_id="123456789012345",
    )


def test_parse_whatsapp_credentials_alternate_keys():
    payload = json.dumps(
        {"token": "TOK", "phone_id": "9876543210"}
    ).encode("utf-8")
    creds = parse_whatsapp_credentials(payload)
    assert creds.access_token == "TOK"
    assert creds.phone_number_id == "9876543210"


def test_parse_whatsapp_credentials_plain_text_rejected():
    with pytest.raises(SessionInvalidError, match="JSON"):
        parse_whatsapp_credentials(b"plain-token-only")


def test_parse_whatsapp_credentials_missing_phone_number_id():
    payload = json.dumps({"access_token": "TOK"}).encode("utf-8")
    with pytest.raises(SessionInvalidError, match="phone_number_id"):
        parse_whatsapp_credentials(payload)


def test_parse_whatsapp_credentials_non_numeric_phone_id():
    payload = json.dumps(
        {"access_token": "TOK", "phone_number_id": "not-numeric"}
    ).encode("utf-8")
    with pytest.raises(SessionInvalidError, match="numeric"):
        parse_whatsapp_credentials(payload)


def test_to_whatsapp_recipient_iranian_mobile():
    assert to_whatsapp_recipient_e164_digits("09121234567") == "989121234567"


def test_to_whatsapp_recipient_already_e164():
    assert to_whatsapp_recipient_e164_digits("+989121234567") == "989121234567"


def test_to_whatsapp_recipient_invalid_raises():
    with pytest.raises(PermanentWorkerError):
        to_whatsapp_recipient_e164_digits("abc")


def test_resolve_whatsapp_recipient_from_phone():
    payload = _sample_payload(recipient="09129876543", recipient_type="phone_number")
    assert resolve_whatsapp_recipient(payload) == "989129876543"


def test_resolve_whatsapp_recipient_from_metadata_wa_id():
    payload = _sample_payload(
        recipient="ignored",
        recipient_type="channel_handle",
        metadata={"wa_id": "989001112233"},
    )
    assert resolve_whatsapp_recipient(payload) == "989001112233"


def test_resolve_whatsapp_recipient_missing_phone_fails():
    payload = _sample_payload(
        recipient="",
        recipient_type="channel_handle",
        metadata={},
    )
    with pytest.raises(PermanentWorkerError):
        resolve_whatsapp_recipient(payload)


@pytest.mark.asyncio
async def test_send_whatsapp_text_message_success(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "messaging_product": "whatsapp",
                "messages": [{"id": "wamid.HBgMOTg5MTIxMjM0NTY3FQIAERgSQjA"}],
            }

    async def fake_request(*args, **kwargs):
        assert "Authorization" in kwargs["headers"]
        assert kwargs["headers"]["Authorization"].startswith("Bearer ")
        body = kwargs["json_body"]
        assert body["messaging_product"] == "whatsapp"
        assert body["to"] == "989121234567"
        assert body["text"]["body"] == "hello"
        return FakeResponse()

    monkeypatch.setattr(whatsapp_connector, "request_whatsapp_api", fake_request)

    result = await send_whatsapp_text_message(
        credentials=WhatsAppCredentials("TOK", "12345"),
        recipient="989121234567",
        text="hello",
        settings=_live_settings(),
    )
    assert result.success is True
    assert result.status == "delivered"
    assert result.platform_message_id.startswith("wamid.")


@pytest.mark.asyncio
async def test_send_whatsapp_text_message_meta_rate_limit(monkeypatch):
    class FakeResponse:
        status_code = 400

        @staticmethod
        def json():
            return {
                "error": {
                    "message": "Rate limit hit",
                    "type": "OAuthException",
                    "code": 130429,
                }
            }

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(whatsapp_connector, "request_whatsapp_api", fake_request)

    result = await send_whatsapp_text_message(
        credentials=WhatsAppCredentials("TOK", "12345"),
        recipient="989121234567",
        text="hello",
        settings=_live_settings(),
    )
    assert result.success is False
    assert result.status == "failed_retryable"
    assert result.error_code == "whatsapp_rate_limited"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_send_whatsapp_text_message_reengagement_required(monkeypatch):
    class FakeResponse:
        status_code = 400

        @staticmethod
        def json():
            return {
                "error": {
                    "message": "Re-engagement message",
                    "type": "OAuthException",
                    "code": 131047,
                }
            }

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(whatsapp_connector, "request_whatsapp_api", fake_request)

    result = await send_whatsapp_text_message(
        credentials=WhatsAppCredentials("TOK", "12345"),
        recipient="989121234567",
        text="hello",
        settings=_live_settings(),
    )
    assert result.success is False
    assert result.status == "failed_permanent"
    assert result.error_code == "whatsapp_reengagement_required"


@pytest.mark.asyncio
async def test_send_whatsapp_text_message_empty_body():
    result = await send_whatsapp_text_message(
        credentials=WhatsAppCredentials("TOK", "12345"),
        recipient="989121234567",
        text="   ",
        settings=_live_settings(),
    )
    assert result.success is False
    assert result.error_code == "whatsapp_empty_message"


@pytest.mark.asyncio
async def test_deliver_whatsapp_live_missing_session():
    result = await deliver_whatsapp_live(_sample_payload(), _live_settings())
    assert result.success is False
    assert result.error_code == "whatsapp_session_missing"


@pytest.mark.asyncio
async def test_deliver_whatsapp_live_success(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number="wa-business-1",
        label="WA Business",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()

    store_channel_session(
        session,
        account_id=account.id,
        session_type=SessionType.API_TOKEN,
        plaintext=_valid_credentials_json().decode("utf-8"),
    )
    session.commit()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"messages": [{"id": "wamid.TEST123"}]}

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(whatsapp_connector, "request_whatsapp_api", fake_request)

    payload = _sample_payload(account_id=account.id, recipient="09121112233")
    result = await deliver_whatsapp_live(payload, _live_settings())
    assert result.success is True
    assert result.platform_message_id == "wamid.TEST123"
    session.close()


@pytest.mark.asyncio
async def test_deliver_platform_message_routes_to_whatsapp_live(monkeypatch):
    async def fake_deliver(payload, settings):
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="wamid.ROUTED",
        )

    monkeypatch.setattr("workers.delivery.deliver_whatsapp_web_live", fake_deliver)

    payload = _sample_payload()
    settings = _live_settings()
    settings = settings.model_copy(update={"WHATSAPP_DELIVERY_MODE": "web"})
    result = await deliver_platform_message("whatsapp", payload, settings)
    assert result.success is True
    assert result.platform_message_id == "wamid.ROUTED"


@pytest.fixture(autouse=True)
def worker_session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
