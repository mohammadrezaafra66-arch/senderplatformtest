import pytest

from workers.retry import (
    build_retry_queue_payload,
    compute_retry_delay_seconds,
    should_schedule_retry,
)
from workers.payloads import WorkerPayload, WorkerResult


def _payload(attempt: int = 1) -> WorkerPayload:
    return WorkerPayload.model_validate(
        {
            "message_id": 1,
            "campaign_id": 10,
            "contact_id": 20,
            "account_id": 1,
            "platform": "whatsapp",
            "recipient": "09120000000",
            "recipient_type": "phone_number",
            "message_text": "hello",
            "dedupe_key": "dedupe-1",
            "attempt": attempt,
        }
    )


def test_compute_retry_delay_exponential():
    assert compute_retry_delay_seconds(1, 5.0) == 5.0
    assert compute_retry_delay_seconds(2, 5.0) == 10.0
    assert compute_retry_delay_seconds(3, 5.0) == 20.0


def test_should_schedule_retry_respects_max_attempts():
    payload = _payload(attempt=2)
    result = WorkerResult(
        success=False,
        status="failed_retryable",
        retryable=True,
        error_code="whatsapp_web_send_failed",
    )
    assert should_schedule_retry(payload, result, max_retry_attempts=3) is True
    assert should_schedule_retry(_payload(attempt=3), result, max_retry_attempts=3) is False


def test_build_retry_queue_payload_increments_attempt():
    payload = _payload(attempt=1)
    raw = '{"campaign_id":10,"contact_id":20,"channel":"whatsapp","final_text":"hi","phone":"0912","account_id":1,"attempt":1}'
    updated = build_retry_queue_payload(raw, payload)
    assert '"attempt": 2' in updated or '"attempt":2' in updated
