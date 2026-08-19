"""Phase 7 — kill switch, shadow pilot, profile guards. No live vendor calls."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    MessageAttempt,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
    SessionType,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_render import hash_final_text
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.pilot_profiles import (
    LOCAL_TEST,
    PILOT_LIVE,
    PILOT_SHADOW,
    classify_profile,
    require_manual_live_confirmation,
    transport_kill_switch_engaged,
)
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.config import WorkerSettings, get_worker_settings
from workers.delivery import deliver_platform_message
from workers.payloads import WorkerPayload

IRAN = ZoneInfo("Asia/Tehran")


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setenv("REAL_MESSAGE_SENDING_ENABLED", "false")
    monkeypatch.setenv("CHANNEL_CONNECTORS_ENABLED", "false")
    monkeypatch.setenv("REAL_QUEUE_PUSH_ENABLED", "false")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


@pytest.fixture(autouse=True)
def _cleanup_db(pg_session_factory):
    """Prevent the shadow pilot campaign test rows from leaking into sender assignment tests."""

    yield

    session = pg_session_factory()
    try:
        campaign_ids = [
            row_id
            for (row_id,) in session.query(Campaign.id)
            .filter(Campaign.title == "p7-shadow")
            .all()
        ]
        if not campaign_ids:
            return

        account_ids = [
            row_id
            for (row_id,) in session.query(Account.id)
            .filter(Account.label == "p7-shadow")
            .all()
        ]

        contact_ids = [
            row_id
            for (row_id,) in session.query(Contact.id)
            .filter(Contact.campaign_id.in_(campaign_ids))
            .all()
        ]

        session.query(StagedQueueItem).filter(
            StagedQueueItem.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(RenderedMessage).filter(
            RenderedMessage.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)

        # FK-safe order:
        # CampaignRecipient.final_message_id → Message.id
        session.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(CampaignAccount).filter(
            CampaignAccount.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)

        message_ids = [
            row_id
            for (row_id,) in session.query(Message.id)
            .filter(Message.campaign_id.in_(campaign_ids))
            .all()
        ]
        if message_ids:
            session.query(MessageAttempt).filter(
                MessageAttempt.message_id.in_(message_ids)
            ).delete(synchronize_session=False)

        session.query(Message).filter(Message.campaign_id.in_(campaign_ids)).delete(
            synchronize_session=False
        )

        if contact_ids:
            session.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
                synchronize_session=False
            )

        # Contact.campaign_id → Campaign.id
        session.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False
        )

        # Shadow pilot accounts have channel sessions stored.
        if account_ids:
            from core_engine.models import ChannelSession

            session.query(ChannelSession).filter(
                ChannelSession.account_id.in_(account_ids)
            ).delete(synchronize_session=False)
            session.query(Account).filter(Account.id.in_(account_ids)).delete(
                synchronize_session=False
            )

        session.commit()
    except Exception:
        # If any FK-sensitive delete fails, roll back first so we don't cascade
        # into PendingRollbackError during teardown.
        session.rollback()
        raise
    finally:
        session.close()


def _rubika_payload() -> WorkerPayload:
    return WorkerPayload(
        message_id=1,
        campaign_id=10,
        contact_id=20,
        account_id=7,
        platform="rubika",
        recipient="989120000000",
        recipient_type="phone",
        message_text="سلام، این یک پیام آزمایشی داخلی است.",
        dedupe_key="phase7-shadow-1",
    )


@pytest.mark.asyncio
async def test_kill_switch_off_never_calls_rubika_transport(monkeypatch):
    bot = AsyncMock(side_effect=AssertionError("bot transport"))
    user = AsyncMock(side_effect=AssertionError("user transport"))
    monkeypatch.setattr("workers.delivery.deliver_rubika_live", bot)
    monkeypatch.setattr(
        "workers.connectors.rubika_user.deliver_rubika_user_live",
        user,
        raising=False,
    )
    payload = _rubika_payload()
    settings = WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=False,
        CHANNEL_CONNECTORS_ENABLED=True,
        RUBIKA_DELIVERY_MODE="bot_api",
        RUBIKA_USER_ACCOUNT_ENABLED=True,
    )
    result = await deliver_platform_message("rubika", payload, settings)
    assert result.success is False
    assert result.error_code == "real_send_disabled"
    assert transport_kill_switch_engaged(settings) is True
    bot.assert_not_called()
    user.assert_not_called()

    settings_user = WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=False,
        CHANNEL_CONNECTORS_ENABLED=True,
        RUBIKA_DELIVERY_MODE="user_account",
        RUBIKA_USER_ACCOUNT_ENABLED=True,
    )
    result2 = await deliver_platform_message("rubika", payload, settings_user)
    assert result2.error_code == "real_send_disabled"
    bot.assert_not_called()
    user.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_and_dry_run_do_not_call_transport(monkeypatch):
    bot = AsyncMock(side_effect=AssertionError("bot transport"))
    monkeypatch.setattr("workers.delivery.deliver_rubika_live", bot)
    payload = _rubika_payload()
    dry = await deliver_platform_message(
        "rubika", payload, WorkerSettings(DRY_RUN=True, REAL_MESSAGE_SENDING_ENABLED=True)
    )
    assert dry.status == "dry_run"
    shadow = await deliver_platform_message(
        "rubika",
        payload,
        WorkerSettings(
            DRY_RUN=False,
            SHADOW_MODE=True,
            SHADOW_PHONE_NUMBER="+989000000000",
            REAL_MESSAGE_SENDING_ENABLED=True,
        ),
    )
    assert shadow.status == "shadow_sent"
    bot.assert_not_called()


def test_profile_classification():
    shadow = WorkerSettings(
        REAL_MESSAGE_SENDING_ENABLED=False,
        CHANNEL_CONNECTORS_ENABLED=False,
        DRY_RUN=False,
        SHADOW_MODE=False,
    )
    assert classify_profile(shadow, queue_push=True) == PILOT_SHADOW
    assert classify_profile(shadow, queue_push=False) == LOCAL_TEST
    live = WorkerSettings(
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        DRY_RUN=False,
        SHADOW_MODE=False,
    )
    assert classify_profile(live, queue_push=True) == PILOT_LIVE


def test_manual_live_guard_refuses_without_flags(monkeypatch):
    monkeypatch.delenv("MANUAL_LIVE_TEST", raising=False)
    monkeypatch.delenv("PILOT_CONFIRM", raising=False)
    with pytest.raises(RuntimeError, match="MANUAL_LIVE_TEST"):
        require_manual_live_confirmation()
    monkeypatch.setenv("MANUAL_LIVE_TEST", "1")
    with pytest.raises(RuntimeError, match="PILOT_CONFIRM"):
        require_manual_live_confirmation()
    monkeypatch.setenv("PILOT_CONFIRM", "SEND")
    require_manual_live_confirmation()


def test_ci_defaults_cannot_live_send():
    settings = get_settings()
    assert settings.REAL_MESSAGE_SENDING_ENABLED is False
    assert settings.CHANNEL_CONNECTORS_ENABLED is False
    assert settings.OPS_LIVE_SEND_API_ENABLED is False
    workers = get_worker_settings()
    assert workers.REAL_MESSAGE_SENDING_ENABLED is False
    assert workers.CHANNEL_CONNECTORS_ENABLED is False


def test_openai_store_false_on_provider_call():
    from core_engine.services.message_variation.openai_provider import (
        OpenAIMessageVariationProvider,
    )
    from core_engine.services.message_variation.prompt import SYSTEM_INSTRUCTION_FA

    class _Resp:
        output_text = '{"variations":[{"text":"سلام {{first_name}} آزمایش","label":"a"},{"text":"{{first_name}} سلام تست","label":"b"},{"text":"درود {{first_name}} آزمایشی","label":"c"}]}'
        usage = None

    client = MagicMock()
    client.responses.create.return_value = _Resp()
    provider = OpenAIMessageVariationProvider(client=client, model="gpt-4o-mini")
    provider.generate_variations(
        template_text="سلام {{first_name}}، این یک پیام آزمایشی داخلی است.",
        protected_placeholders=["first_name"],
        requested_count=3,
        language="fa",
        instructions=SYSTEM_INSTRUCTION_FA,
    )
    kwargs = client.responses.create.call_args.kwargs
    assert kwargs["store"] is False


def _make_account(session):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98912{abs(hash(uuid.uuid4().hex)) % 10_000_000:07d}",
        label="p7-shadow",
        status=AccountStatus.ACTIVE,
        warming_started_at=datetime.now(IRAN) - timedelta(days=20),
        last_used_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(account)
    session.flush()
    envelope = build_session_envelope(
        phone_number=account.phone_number,
        auth="d" * 32,
        guid=f"u{account.id}",
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
    session.refresh(account)
    return account


def test_shadow_campaign_freeze_restart_and_kill_switch(pg_session_factory, monkeypatch):
    session = pg_session_factory()
    account = _make_account(session)
    campaign = Campaign(
        name=f"p7-{uuid.uuid4().hex[:8]}",
        title="p7-shadow",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.DRAFT.value,
        template_text="سلام {{first_name}}، این یک پیام آزمایشی داخلی است.",
        use_gpt=False,
        include_products=False,
    )
    session.add(campaign)
    session.flush()
    session.add(
        CampaignAccount(
            campaign_id=campaign.id, account_id=account.id, priority=1, enabled=True
        )
    )
    contact = Contact(
        first_name="آزمایش",
        phone="+989120000111",
        phone_e164="+989120000111",
        consent_status="allowed",
        campaign_id=campaign.id,
    )
    session.add(contact)
    session.flush()
    session.add(
        CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id)
    )
    session.commit()
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    session.expire_all()
    rendered = (
        session.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign.id)
        .one()
    )
    assert rendered.final_text
    assert "آزمایش" in rendered.final_text
    digest = hash_final_text(rendered.final_text)
    meta = (rendered.queue_payload or {}).get("metadata") or {}
    assert meta.get("final_text_sha256") == digest
    message = session.query(Message).filter(Message.campaign_id == campaign.id).one()
    assert message.account_id == account.id
    campaign_id = campaign.id
    text = rendered.final_text
    sender = message.account_id
    session.close()

    session2 = pg_session_factory()
    again = (
        session2.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign_id)
        .one()
    )
    assert again.final_text == text
    assert hash_final_text(again.final_text) == digest
    msg2 = session2.query(Message).filter(Message.campaign_id == campaign_id).one()
    assert msg2.account_id == sender
    session2.close()


@pytest.mark.asyncio
async def test_shadow_worker_path_transport_blocked(monkeypatch):
    bot = AsyncMock()
    monkeypatch.setattr("workers.delivery.deliver_rubika_live", bot)
    result = await deliver_platform_message(
        "rubika",
        _rubika_payload(),
        WorkerSettings(
            DRY_RUN=False,
            SHADOW_MODE=False,
            REAL_MESSAGE_SENDING_ENABLED=False,
            CHANNEL_CONNECTORS_ENABLED=True,
        ),
    )
    assert result.error_code == "real_send_disabled"
    bot.assert_not_called()
