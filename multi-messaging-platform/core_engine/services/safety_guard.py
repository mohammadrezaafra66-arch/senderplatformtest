"""گیت ایمنی Phase 4 — جلوگیری از ارسال واقعی و push به صف Worker."""

from __future__ import annotations

from core_engine.config import get_settings


class SafetyViolationError(RuntimeError):
    """تلاش برای فعال‌سازی رفتار خطرناک در حالت dry-run/debug."""


def get_safety_status() -> dict[str, bool]:
    settings = get_settings()
    return {
        "phase_4_debug_mode": settings.PHASE_4_DEBUG_MODE,
        "real_queue_push_enabled": settings.REAL_QUEUE_PUSH_ENABLED,
        "real_message_sending_enabled": settings.REAL_MESSAGE_SENDING_ENABLED,
        "worker_execution_enabled": settings.WORKER_EXECUTION_ENABLED,
        "channel_connectors_enabled": settings.CHANNEL_CONNECTORS_ENABLED,
    }


def assert_real_sending_disabled() -> None:
    if get_settings().REAL_MESSAGE_SENDING_ENABLED:
        raise SafetyViolationError(
            "Real message sending is disabled by policy. "
            "Phase 4 must use database staging only."
        )


def assert_real_queue_push_disabled() -> None:
    if get_settings().REAL_QUEUE_PUSH_ENABLED:
        raise SafetyViolationError(
            "Real Redis worker queue push is disabled by policy. "
            "Phase 4 must use database staging only."
        )


def assert_worker_execution_disabled() -> None:
    if get_settings().WORKER_EXECUTION_ENABLED:
        raise SafetyViolationError(
            "Worker execution is disabled by policy during Phase 4 debug staging."
        )


def assert_channel_connectors_disabled() -> None:
    if get_settings().CHANNEL_CONNECTORS_ENABLED:
        raise SafetyViolationError(
            "External channel connectors are disabled by policy during Phase 4."
        )


def assert_phase_4_staging_safe() -> None:
    """برای endpointهای آماده‌سازی Phase 4 — همه گیت‌های خطرناک باید بسته باشند."""
    assert_real_sending_disabled()
    assert_real_queue_push_disabled()
    assert_worker_execution_disabled()
    assert_channel_connectors_disabled()
