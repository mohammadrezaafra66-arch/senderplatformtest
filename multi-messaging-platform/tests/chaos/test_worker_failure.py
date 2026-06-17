import pytest

from core_engine.models import SendStatus
from core_engine.services.message_dispatch import dispatch_message, set_send_to_channel
from core_engine.services.message_log import get_message_logs
from core_engine.services.metrics_service import get_metrics_output
from core_engine.tasks import send_message_task
from tests.chaos.conftest import SAMPLE_PAYLOAD
from tests.monitoring.helpers import sum_counter_values


def _metrics_text() -> str:
    content, _ = get_metrics_output()
    return content.decode()


@pytest.mark.chaos
def test_worker_send_exception_records_failure(normal_send_settings, bypass_consent):
    before_failed = sum_counter_values(_metrics_text(), "messages_sent_failed_total")

    def exploding_send(_payload):
        raise RuntimeError("simulated worker crash")

    set_send_to_channel(exploding_send)
    try:
        result = dispatch_message(SAMPLE_PAYLOAD)
    finally:
        set_send_to_channel(None)

    assert result["dispatched"] is False
    assert result["status"] == SendStatus.FAILED_RETRYABLE.value

    logs = get_message_logs()
    assert len(logs) == 1
    assert logs[0].status == SendStatus.FAILED_RETRYABLE.value
    assert "simulated worker crash" in str(logs[0].metadata.get("send_error", ""))

    after_failed = sum_counter_values(_metrics_text(), "messages_sent_failed_total")
    assert after_failed > before_failed


@pytest.mark.chaos
def test_celery_task_retry_policy_configured():
    assert send_message_task.max_retries == 3


@pytest.mark.chaos
def test_celery_task_calls_retry_on_unhandled_exception(monkeypatch):
    retry_invoked = {"value": False}

    def flaky_dispatch(_payload):
        raise RuntimeError("transient worker fault")

    def fake_retry(exc=None, **kwargs):
        retry_invoked["value"] = True
        raise exc

    monkeypatch.setattr(
        "core_engine.services.message_dispatch.dispatch_message",
        flaky_dispatch,
    )
    monkeypatch.setattr(send_message_task, "retry", fake_retry)

    with pytest.raises(RuntimeError, match="transient worker fault"):
        send_message_task.run(SAMPLE_PAYLOAD)

    assert retry_invoked["value"] is True
