import pytest
from redis.exceptions import RedisError

from core_engine.models import SendStatus
from core_engine.services.message_dispatch import dispatch_message, set_push_to_queue, set_send_to_channel
from core_engine.services.message_log import get_message_logs
from core_engine.services.metrics_service import get_metrics_output
from core_engine.services.queue_manager import enqueue_message
from core_engine.services.redis_client import reset_redis_client
from tests.chaos.conftest import SAMPLE_PAYLOAD
from tests.monitoring.helpers import sum_counter_values


def _metrics_text() -> str:
    content, _ = get_metrics_output()
    return content.decode()


@pytest.mark.chaos
def test_redis_push_failure_returns_controlled_error(normal_send_settings, bypass_consent):
    before_rate = sum_counter_values(_metrics_text(), "rate_limit_hits_total")

    def failing_push(_payload):
        raise RedisError("simulated redis connection lost")

    set_push_to_queue(failing_push)
    try:
        result = dispatch_message(SAMPLE_PAYLOAD)
    finally:
        set_push_to_queue(None)

    assert result["dispatched"] is False
    assert result["status"] == SendStatus.FAILED_RETRYABLE.value
    assert "redis" in result.get("error", "").lower() or "Redis" in result.get("error_type", "")

    logs = get_message_logs()
    assert len(logs) == 1
    assert logs[0].status == SendStatus.FAILED_RETRYABLE.value
    assert logs[0].status != SendStatus.BLACKLISTED.value

    after_rate = sum_counter_values(_metrics_text(), "rate_limit_hits_total")
    assert after_rate == before_rate


@pytest.mark.chaos
def test_redis_recovery_allows_enqueue_after_reset(normal_send_settings, bypass_consent):
    def failing_push(_payload):
        raise RedisError("temporary outage")

    set_push_to_queue(failing_push)
    failed = dispatch_message(SAMPLE_PAYLOAD)
    set_push_to_queue(None)
    reset_redis_client()

    assert failed["dispatched"] is False

    recovered = enqueue_message(SAMPLE_PAYLOAD)
    assert recovered["enqueued"] is True
    assert recovered["status"] == SendStatus.QUEUED.value
