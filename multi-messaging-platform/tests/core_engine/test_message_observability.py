"""Phase 4 — outbound status must not call API acceptance delivered."""

from __future__ import annotations

from workers.payloads import WorkerResult
from core_engine.services.message_observability import (
    DELIVERY_UNKNOWN,
    READ_UNKNOWN,
    apply_submission_boundary,
    classify_rubika_connector_result,
    operator_error_text,
    send_status_label_fa,
)


def test_api_ok_without_message_id_is_accepted_not_delivered():
    result = classify_rubika_connector_result(
        WorkerResult(success=True, status="delivered", platform_message_id="rubika-sent")
    )
    assert result.status == "accepted_by_platform"
    assert result.platform_message_id is None
    assert result.status != "delivered"


def test_api_ok_with_message_id_stores_id_and_is_not_delivered():
    result = classify_rubika_connector_result(
        WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="rubika-abc123",
        )
    )
    assert result.status == "accepted_by_platform"
    assert result.platform_message_id == "rubika-abc123"
    assert result.retryable is False


def test_permanent_rejection_stays_permanent():
    result = classify_rubika_connector_result(
        WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="rubika_unauthorized",
            error_message="token=secret",
            retryable=False,
        )
    )
    assert result.status == "failed_permanent"
    assert result.retryable is False
    assert "secret" not in operator_error_text(result.error_message)


def test_temporary_network_failure_is_retryable_before_submit():
    result = classify_rubika_connector_result(
        WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="rubika_timeout",
            retryable=True,
        )
    )
    assert result.status == "failed_retryable"
    assert result.retryable is True


def test_uncertain_result_after_submit_is_not_retried_as_a_new_message():
    incoming = WorkerResult(
        success=False,
        status="failed_retryable",
        error_code="rubika_timeout",
        retryable=True,
    )
    result = apply_submission_boundary(incoming, submitted=True)
    assert result.status == "unknown_external_result"
    assert result.retryable is False
    assert result.success is False


def test_labels_do_not_claim_delivery_or_read():
    assert send_status_label_fa("accepted_by_platform") == "ثبت در پلتفرم"
    assert "تحویل" not in send_status_label_fa("accepted_by_platform")
    assert send_status_label_fa("unknown_external_result") == "نتیجه ارسال نامشخص"
    assert DELIVERY_UNKNOWN == "تحویل نامشخص"
    assert READ_UNKNOWN == "خوانده‌شدن نامشخص"
    assert send_status_label_fa("failed_retryable") == "ناموفق موقت"
    assert send_status_label_fa("failed_permanent") == "ناموفق دائم"
