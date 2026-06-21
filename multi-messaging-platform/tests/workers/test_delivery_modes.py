import pytest

from workers.config import WorkerSettings
from workers.delivery import deliver_platform_message
from workers.payload_adapter import normalize_queue_payload
from workers.payloads import WorkerPayload


def _sample_payload() -> WorkerPayload:
    return WorkerPayload(
        message_id=1,
        campaign_id=10,
        contact_id=20,
        account_id=1,
        platform="bale",
        recipient="+989120000000",
        recipient_type="phone_number",
        message_text="hello",
        dedupe_key="dedupe-1",
    )


def test_normalize_staged_queue_payload():
    raw = {
        "campaign_id": 5,
        "contact_id": 9,
        "channel": "bale",
        "final_text": "سلام",
        "phone": "+989121111111",
        "account_id": 2,
    }
    normalized = normalize_queue_payload(raw)
    assert normalized["platform"] == "bale"
    assert normalized["message_text"] == "سلام"
    assert normalized["recipient"] == "+989121111111"
    assert normalized["message_id"] == "5:9"
    assert normalized["dedupe_key"] == "c5-ct9-a1"


def test_normalize_bale_prefers_channel_handle():
    raw = {
        "campaign_id": 5,
        "contact_id": 9,
        "channel": "bale",
        "final_text": "سلام",
        "phone": "+989121111111",
        "channel_handle": "123456789",
        "account_id": 2,
    }
    normalized = normalize_queue_payload(raw)
    assert normalized["recipient"] == "123456789"
    assert normalized["recipient_type"] == "channel_handle"
    assert normalized["metadata"]["channel_handle"] == "123456789"


def test_normalize_telegram_prefers_channel_handle():
    raw = {
        "campaign_id": 5,
        "contact_id": 9,
        "channel": "telegram",
        "final_text": "سلام",
        "phone": "+989121111111",
        "channel_handle": "987654321",
        "account_id": 2,
    }
    normalized = normalize_queue_payload(raw)
    assert normalized["recipient"] == "987654321"
    assert normalized["recipient_type"] == "channel_handle"


def test_normalize_whatsapp_prefers_phone():
    raw = {
        "campaign_id": 5,
        "contact_id": 9,
        "channel": "whatsapp",
        "final_text": "سلام",
        "phone": "09121234567",
        "channel_handle": "should-not-win",
        "account_id": 2,
    }
    normalized = normalize_queue_payload(raw)
    assert normalized["recipient"] == "09121234567"
    assert normalized["recipient_type"] == "phone_number"


def test_normalize_rubika_prefers_channel_handle():
    raw = {
        "campaign_id": 5,
        "contact_id": 9,
        "channel": "rubika",
        "final_text": "سلام",
        "phone": "+989121111111",
        "channel_handle": "b0QFtabc1I02214b529f1d60c9ce5b08",
        "account_id": 2,
    }
    normalized = normalize_queue_payload(raw)
    assert normalized["recipient"] == "b0QFtabc1I02214b529f1d60c9ce5b08"
    assert normalized["recipient_type"] == "channel_handle"


@pytest.mark.asyncio
async def test_deliver_dry_run_success():
    settings = WorkerSettings(DRY_RUN=True)
    result = await deliver_platform_message("bale", _sample_payload(), settings)
    assert result.success is True
    assert result.status == "dry_run"
    assert result.platform_message_id


@pytest.mark.asyncio
async def test_deliver_shadow_requires_phone():
    settings = WorkerSettings(SHADOW_MODE=True, SHADOW_PHONE_NUMBER="")
    result = await deliver_platform_message("bale", _sample_payload(), settings)
    assert result.success is False
    assert result.error_code == "shadow_phone_missing"


@pytest.mark.asyncio
async def test_deliver_shadow_success():
    settings = WorkerSettings(SHADOW_MODE=True, SHADOW_PHONE_NUMBER="+989000000000")
    result = await deliver_platform_message("telegram", _sample_payload(), settings)
    assert result.success is True
    assert result.status == "shadow_sent"


@pytest.mark.asyncio
async def test_deliver_live_disabled_without_flags():
    settings = WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=False,
    )
    result = await deliver_platform_message("bale", _sample_payload(), settings)
    assert result.success is False
    assert result.error_code == "real_send_disabled"


@pytest.mark.asyncio
async def test_deliver_live_connectors_disabled():
    settings = WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=False,
    )
    result = await deliver_platform_message("bale", _sample_payload(), settings)
    assert result.success is False
    assert result.error_code == "connectors_disabled"
