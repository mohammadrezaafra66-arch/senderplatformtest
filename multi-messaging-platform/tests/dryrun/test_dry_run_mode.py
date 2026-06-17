import pytest

from core_engine.config import get_settings
from core_engine.models import SendStatus
from core_engine.services.message_dispatch import dispatch_message, set_send_to_channel
from core_engine.services.message_log import clear_message_logs, get_message_logs
from core_engine.services.queue_manager import enqueue_message


SAMPLE_PAYLOAD = {
    "contact_id": 42,
    "campaign_id": 7,
    "platform": "whatsapp",
    "chat_identifier": "09121111111",
    "message_text": "Dry-run integration message",
    "attempt_count": 0,
}


@pytest.fixture(autouse=True)
def reset_message_logs():
    clear_message_logs()
    yield
    clear_message_logs()


@pytest.fixture
def dry_run_enabled(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("SHADOW_MODE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_enqueue_message_dry_run_skips_queue_and_logs(dry_run_enabled):
    result = enqueue_message(SAMPLE_PAYLOAD)

    assert result["enqueued"] is False
    assert result["status"] == SendStatus.DRY_RUN.value
    logs = get_message_logs()
    assert len(logs) == 1
    assert logs[0].status == SendStatus.DRY_RUN.value
    assert logs[0].contact_id == 42
    assert logs[0].chat_identifier == "09121111111"


def test_dispatch_message_dry_run_does_not_call_channel_send(dry_run_enabled):
    calls: list[object] = []

    def fake_send(_payload):
        calls.append(_payload)
        return {"ok": True}

    set_send_to_channel(fake_send)
    try:
        result = dispatch_message(SAMPLE_PAYLOAD)
    finally:
        set_send_to_channel(None)

    assert result["dispatched"] is False
    assert result["status"] == SendStatus.DRY_RUN.value
    assert calls == []
    assert get_message_logs()[0].status == SendStatus.DRY_RUN.value
