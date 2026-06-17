import os
import time

import pytest

from core_engine.models import SendStatus
from core_engine.services.message_dispatch import set_send_to_channel
from core_engine.services.metrics_service import get_metrics_output
from core_engine.services.queue_manager import enqueue_message, send_message_via_queue
from tests.monitoring.helpers import sum_counter_values
from tests.stress.conftest import STRESS_MAX_SECONDS, STRESS_MESSAGE_COUNT


def _metrics_text() -> str:
    content, _ = get_metrics_output()
    return content.decode()


def _sample_payload(index: int) -> dict:
    return {
        "contact_id": index + 1,
        "campaign_id": 1,
        "platform": "whatsapp",
        "account_id": 1,
        "chat_identifier": f"0912{index:07d}",
        "message_text": f"stress message {index}",
        "attempt_count": 0,
    }


@pytest.mark.stress
def test_load_queue_processing(normal_send_settings, bypass_consent):
    before = sum_counter_values(_metrics_text(), "messages_queued_total")

    def fast_send(_payload):
        return {"ok": True}

    set_send_to_channel(fast_send)
    failures: list[object] = []
    started = time.perf_counter()

    try:
        for index in range(STRESS_MESSAGE_COUNT):
            try:
                result = send_message_via_queue(_sample_payload(index))
                if result.get("status") not in {
                    SendStatus.QUEUED.value,
                    SendStatus.DELIVERED.value,
                } and result.get("enqueued") is not False:
                    failures.append(result)
            except Exception as exc:
                failures.append(exc)
    finally:
        set_send_to_channel(None)

    elapsed = time.perf_counter() - started
    after = sum_counter_values(_metrics_text(), "messages_queued_total")

    assert failures == [], f"unexpected failures: {failures[:5]}"
    assert after - before >= STRESS_MESSAGE_COUNT
    assert elapsed < STRESS_MAX_SECONDS, (
        f"processed {STRESS_MESSAGE_COUNT} messages in {elapsed:.2f}s "
        f"(limit {STRESS_MAX_SECONDS}s); set STRESS_MAX_SECONDS to relax"
    )


@pytest.mark.stress
def test_load_enqueue_only(normal_send_settings, bypass_consent):
    before = sum_counter_values(_metrics_text(), "messages_queued_total")
    started = time.perf_counter()

    for index in range(min(STRESS_MESSAGE_COUNT, int(os.environ.get("STRESS_ENQUEUE_ONLY_COUNT", "500")))):
        result = enqueue_message(_sample_payload(index))
        assert result["enqueued"] is True
        assert result["status"] == SendStatus.QUEUED.value

    elapsed = time.perf_counter() - started
    after = sum_counter_values(_metrics_text(), "messages_queued_total")
    enqueued = min(STRESS_MESSAGE_COUNT, int(os.environ.get("STRESS_ENQUEUE_ONLY_COUNT", "500")))

    assert after - before >= enqueued
    assert elapsed < STRESS_MAX_SECONDS
