"""Canonical outbound status classification.

API acceptance is not delivery. Seen/read stay unknown unless a receipt exists.
Rubika connectors do not currently provide delivery or read receipts.
"""

from __future__ import annotations

import re

from workers.payloads import WorkerResult

DELIVERY_UNKNOWN = "تحویل نامشخص"
READ_UNKNOWN = "خوانده‌شدن نامشخص"

PLATFORM_ACCEPTED = "accepted_by_platform"
UNKNOWN_EXTERNAL_RESULT = "unknown_external_result"

_FABRICATED_IDS = frozenset({"rubika-sent", "rubika-user-sent", "none", "null"})
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+\S+|token[=:]\S+|authorization[=:]\S+|session[=:]\S+)"
)

SEND_STATUS_LABELS_FA = {
    "pending": "آماده",
    "queued": "در صف",
    "processing": "در حال ارسال",
    "accepted_by_worker": "در حال ارسال",
    "accepted_by_platform": "ثبت در پلتفرم",
    "delivered": "تحویل‌شده",
    "read": "خوانده‌شده",
    "failed_retryable": "ناموفق موقت",
    "failed_permanent": "ناموفق دائم",
    "unknown_external_result": "نتیجه ارسال نامشخص",
    "dry_run": "آزمایشی",
    "shadow_sent": "ارسال سایه",
    "opted_out": "انصراف",
    "blacklisted": "مسدود",
}


def send_status_label_fa(status: str | None) -> str:
    key = (status or "").strip().lower()
    return SEND_STATUS_LABELS_FA.get(key, key or "نامشخص")


def operator_error_text(message: str | None) -> str:
    text = (message or "").strip()
    if not text:
        return "ارسال ناموفق بود."
    return _SECRET_RE.sub("[redacted]", text)[:240]


def _real_platform_message_id(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.lower() in _FABRICATED_IDS:
        return None
    return text


def classify_rubika_connector_result(result: WorkerResult) -> WorkerResult:
    """Map a Rubika connector result onto the canonical status names."""
    status = (result.status or "").strip().lower()
    if result.success and status in {"delivered", "accepted_by_platform", "read"}:
        return result.model_copy(
            update={
                "status": PLATFORM_ACCEPTED,
                "platform_message_id": _real_platform_message_id(result.platform_message_id),
                "retryable": False,
            }
        )
    if status == "failed_retryable" or result.retryable:
        return result.model_copy(
            update={
                "status": "failed_retryable",
                "error_message": operator_error_text(result.error_message),
                "retryable": True,
            }
        )
    return result.model_copy(
        update={
            "status": "failed_permanent" if not result.success else result.status,
            "error_message": operator_error_text(result.error_message) if result.error_message else result.error_message,
            "retryable": False,
        }
    )


def apply_submission_boundary(result: WorkerResult, *, submitted: bool) -> WorkerResult:
    """After the connector may have accepted, do not schedule a different retry."""
    if not submitted or result.success:
        return result
    if result.retryable or result.status == "failed_retryable":
        return result.model_copy(
            update={
                "success": False,
                "status": UNKNOWN_EXTERNAL_RESULT,
                "retryable": False,
                "error_code": result.error_code or UNKNOWN_EXTERNAL_RESULT,
                "error_message": operator_error_text(
                    result.error_message or "نتیجه ارسال پس از ثبت در کانکتور نامشخص است."
                ),
            }
        )
    return result
