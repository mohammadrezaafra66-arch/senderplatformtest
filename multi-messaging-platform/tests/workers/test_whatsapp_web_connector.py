import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, ChannelSession, PlatformType
from core_engine.services.whatsapp_web_session import store_whatsapp_web_session
from workers.config import WorkerSettings
from workers.connectors.whatsapp_web import deliver_whatsapp_web_live
from workers.delivery import deliver_platform_message
from workers.payloads import WorkerPayload, WorkerResult
from workers.whatsapp_web.playwright_sender import WhatsAppWebSendResult


def _live_settings() -> WorkerSettings:
    return WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        WHATSAPP_DELIVERY_MODE="web",
        WHATSAPP_WEB_HEADLESS=True,
        WHATSAPP_WEB_SEND_TIMEOUT_SECONDS=5,
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
        message_text="سلام از واتساپ وب",
        dedupe_key="dedupe-wa-web-1",
    )
    base.update(overrides)
    return WorkerPayload.model_validate(base)


@pytest.fixture(autouse=True)
def worker_session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_deliver_whatsapp_web_missing_session():
    result = await deliver_whatsapp_web_live(_sample_payload(), _live_settings())
    assert result.success is False
    assert result.error_code == "whatsapp_web_session_missing"


@pytest.mark.asyncio
async def test_deliver_whatsapp_web_not_linked(pg_session_factory, tmp_path):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number="09121112244",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()

    profile_dir = tmp_path / f"account-{account.id}"
    (profile_dir / "Default").mkdir(parents=True)
    store_whatsapp_web_session(
        session,
        account_id=account.id,
        linked=False,
        phone=account.phone_number,
        profile_dir=profile_dir,
    )
    session.commit()

    result = await deliver_whatsapp_web_live(
        _sample_payload(account_id=account.id),
        _live_settings(),
    )
    assert result.success is False
    assert result.error_code == "whatsapp_web_not_linked"

    session.query(ChannelSession).filter(ChannelSession.account_id == account.id).delete()
    session.query(Account).filter(Account.id == account.id).delete()
    session.commit()
    session.close()


@pytest.mark.asyncio
async def test_deliver_whatsapp_web_success(monkeypatch, pg_session_factory, tmp_path):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number="09121112255",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()

    profile_dir = tmp_path / f"account-{account.id}"
    (profile_dir / "Default").mkdir(parents=True)
    store_whatsapp_web_session(
        session,
        account_id=account.id,
        linked=True,
        phone=account.phone_number,
        profile_dir=profile_dir,
    )
    session.commit()

    async def fake_send(profile_dir_arg, recipient, text, *, headless, timeout_ms, **kwargs):
        assert recipient == "989121112255"
        assert text == "سلام از واتساپ وب"
        return WhatsAppWebSendResult(
            message_id="wa-web-test-123",
            recipient_digits=recipient,
        )

    async def fake_assert(**_kwargs):
        return None

    monkeypatch.setattr(
        "workers.connectors.whatsapp_web.assert_whatsapp_send_allowed",
        fake_assert,
    )
    monkeypatch.setattr(
        "workers.connectors.whatsapp_web.send_whatsapp_web_message",
        fake_send,
    )

    result = await deliver_whatsapp_web_live(
        _sample_payload(account_id=account.id, recipient="09121112255"),
        _live_settings(),
    )
    assert result.success is True
    assert result.platform_message_id == "wa-web-test-123"

    session.query(ChannelSession).filter(ChannelSession.account_id == account.id).delete()
    session.query(Account).filter(Account.id == account.id).delete()
    session.commit()
    session.close()


@pytest.mark.asyncio
async def test_deliver_platform_message_routes_to_whatsapp_web(monkeypatch):
    async def fake_deliver(payload, settings):
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="wa-web.ROUTED",
        )

    monkeypatch.setattr("workers.delivery.deliver_whatsapp_web_live", fake_deliver)

    result = await deliver_platform_message("whatsapp", _sample_payload(), _live_settings())
    assert result.success is True
    assert result.platform_message_id == "wa-web.ROUTED"


@pytest.mark.asyncio
async def test_deliver_platform_message_routes_to_whatsapp_cloud_api(monkeypatch):
    async def fake_deliver(payload, settings):
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="wamid.ROUTED",
        )

    monkeypatch.setattr("workers.delivery.deliver_whatsapp_cloud_live", fake_deliver)

    settings = _live_settings().model_copy(update={"WHATSAPP_DELIVERY_MODE": "cloud_api"})
    result = await deliver_platform_message("whatsapp", _sample_payload(), settings)
    assert result.success is True
    assert result.platform_message_id == "wamid.ROUTED"
