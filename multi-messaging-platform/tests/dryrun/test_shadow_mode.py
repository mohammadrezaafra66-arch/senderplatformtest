import pytest

from core_engine.config import get_settings
from core_engine.models import SendStatus
from core_engine.services.message_dispatch import dispatch_message, set_send_to_channel
from core_engine.services.message_log import clear_message_logs, get_message_logs
from core_engine.services.queue_manager import enqueue_message


SHADOW_PHONE = "09999999999"
ORIGINAL_PHONE = "09122222222"

SAMPLE_PAYLOAD = {
    "contact_id": 99,
    "campaign_id": 3,
    "platform": "telegram",
    "chat_identifier": ORIGINAL_PHONE,
    "message_text": "Shadow mode test message",
    "attempt_count": 1,
}


@pytest.fixture(autouse=True)
def reset_message_logs():
    clear_message_logs()
    yield
    clear_message_logs()


@pytest.fixture
def shadow_mode_enabled(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("SHADOW_MODE", "true")
    monkeypatch.setenv("SHADOW_PHONE_NUMBER", SHADOW_PHONE)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_enqueue_message_shadow_replaces_chat_identifier_only(shadow_mode_enabled):
    result = enqueue_message(SAMPLE_PAYLOAD)

    assert result["enqueued"] is True
    assert result["status"] == SendStatus.SHADOW_SENT.value
    assert result["queue_payload"]["chat_identifier"] == SHADOW_PHONE
    assert result["original_chat_identifier"] == ORIGINAL_PHONE
    assert result["queue_payload"]["contact_id"] == 99

    logs = get_message_logs()
    assert len(logs) == 1
    assert logs[0].status == SendStatus.SHADOW_SENT.value
    assert logs[0].chat_identifier == SHADOW_PHONE
    assert logs[0].original_chat_identifier == ORIGINAL_PHONE
    assert logs[0].contact_id == 99


def test_dispatch_message_shadow_sends_to_shadow_phone(shadow_mode_enabled):
    captured: list[str] = []

    def fake_send(payload):
        captured.append(payload.chat_identifier)
        return {"ok": True}

    set_send_to_channel(fake_send)
    try:
        result = dispatch_message(SAMPLE_PAYLOAD)
    finally:
        set_send_to_channel(None)

    assert result["status"] == SendStatus.SHADOW_SENT.value
    assert result["dispatch_chat_identifier"] == SHADOW_PHONE
    assert result["original_chat_identifier"] == ORIGINAL_PHONE
    assert captured == [SHADOW_PHONE]
    assert get_message_logs()[0].status == SendStatus.SHADOW_SENT.value
