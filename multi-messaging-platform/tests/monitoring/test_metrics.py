import pytest

from core_engine.config import get_settings
from core_engine.services.metrics_service import get_metrics_output
from core_engine.services.queue_manager import enqueue_message
from tests.monitoring.helpers import metric_body_has_nonzero_histogram, sum_counter_values


SAMPLE_PAYLOAD = {
    "contact_id": 10,
    "campaign_id": 2,
    "platform": "whatsapp",
    "account_id": 5,
    "chat_identifier": "09120000000",
    "message_text": "Metrics test message",
    "attempt_count": 0,
}


def _metrics_text() -> str:
    content, _ = get_metrics_output()
    return content.decode()


def test_metrics_endpoint_returns_200(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.content
    body = response.text
    assert "messages_queued_total" in body
    assert "message_processing_seconds" in body


def test_counters_increment(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("SHADOW_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "core_engine.services.queue_manager.check_consent_or_log",
        lambda *args, **kwargs: None,
    )

    before = sum_counter_values(
        _metrics_text(),
        "messages_queued_total",
        label_fragment='platform="whatsapp"',
    )
    enqueue_message(SAMPLE_PAYLOAD)
    after = sum_counter_values(
        _metrics_text(),
        "messages_queued_total",
        label_fragment='platform="whatsapp"',
    )
    assert after == before + 1


def test_histogram_records_time():
    from core_engine.monitoring.metrics import observe_processing_time

    observe_processing_time("telegram", "3", 0.42)
    body = _metrics_text()
    assert metric_body_has_nonzero_histogram(body, "message_processing_seconds")
    assert 'platform="telegram"' in body
    assert 'account_id="3"' in body
