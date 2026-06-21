"""End-to-end session wiring: store session → worker delivery (mocked HTTP)."""

import json

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, PlatformType
from core_engine.services.account_session_wiring import register_api_token_session
from workers.config import WorkerSettings
from workers.connectors import bale as bale_connector
from workers.connectors import rubika as rubika_connector
from workers.connectors import telegram as telegram_connector
from workers.delivery import deliver_platform_message
from workers.payloads import WorkerPayload


@pytest.fixture(autouse=True)
def worker_session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    from workers.config import get_worker_settings

    get_worker_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


def _live_settings() -> WorkerSettings:
    return WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
    )


def _payload(account_id: int, platform: str, handle: str) -> WorkerPayload:
    return WorkerPayload.model_validate(
        {
            "message_id": 1,
            "campaign_id": 10,
            "contact_id": 20,
            "account_id": account_id,
            "platform": platform,
            "recipient": handle,
            "recipient_type": "channel_handle",
            "message_text": "Phase 8.6 wiring test",
            "dedupe_key": f"phase8-{platform}-{account_id}",
            "metadata": {"channel_handle": handle},
        }
    )


@pytest.mark.asyncio
async def test_bale_session_wiring_to_delivery(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="bale-bot",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()
    register_api_token_session(session, account=account, session_payload="BALE_WIRING_TOKEN")
    session.commit()
    account_id = account.id
    session.close()

    async def fake_request(*_args, **_kwargs):
        class Resp:
            status_code = 200

            def json(self):
                return {"ok": True, "result": {"message_id": 42}}

        return Resp()

    monkeypatch.setattr(bale_connector, "request_bale_api", fake_request)
    result = await deliver_platform_message(
        "bale",
        _payload(account_id, "bale", "12345"),
        _live_settings(),
    )
    assert result.success is True

    cleanup = pg_session_factory()
    from core_engine.models import ChannelSession

    cleanup.query(ChannelSession).filter(ChannelSession.account_id == account_id).delete()
    cleanup.query(Account).filter(Account.id == account_id).delete()
    cleanup.commit()
    cleanup.close()


@pytest.mark.asyncio
async def test_telegram_session_wiring_to_delivery(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.TELEGRAM,
        phone_number="@bot",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()
    register_api_token_session(
        session,
        account=account,
        session_payload=json.dumps({"bot_token": "TG_WIRING"}),
    )
    session.commit()
    account_id = account.id
    session.close()

    async def fake_request(*_args, **_kwargs):
        class Resp:
            status_code = 200

            def json(self):
                return {"ok": True, "result": {"message_id": 99}}

        return Resp()

    monkeypatch.setattr(telegram_connector, "request_telegram_api", fake_request)
    result = await deliver_platform_message(
        "telegram",
        _payload(account_id, "telegram", "@user"),
        _live_settings(),
    )
    assert result.success is True

    cleanup = pg_session_factory()
    from core_engine.models import ChannelSession

    cleanup.query(ChannelSession).filter(ChannelSession.account_id == account_id).delete()
    cleanup.query(Account).filter(Account.id == account_id).delete()
    cleanup.commit()
    cleanup.close()


@pytest.mark.asyncio
async def test_rubika_session_wiring_to_delivery(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number="rubika-bot",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()
    register_api_token_session(session, account=account, session_payload="RUBIKA_WIRING")
    session.commit()
    account_id = account.id
    session.close()

    async def fake_request(*_args, **_kwargs):
        class Resp:
            status_code = 200

            def json(self):
                return {"status": "OK", "data": {"message_id": "777"}}

        return Resp()

    monkeypatch.setattr(rubika_connector, "request_rubika_api", fake_request)
    result = await deliver_platform_message(
        "rubika",
        _payload(account_id, "rubika", "chat-abc"),
        _live_settings(),
    )
    assert result.success is True

    cleanup = pg_session_factory()
    from core_engine.models import ChannelSession

    cleanup.query(ChannelSession).filter(ChannelSession.account_id == account_id).delete()
    cleanup.query(Account).filter(Account.id == account_id).delete()
    cleanup.commit()
    cleanup.close()
