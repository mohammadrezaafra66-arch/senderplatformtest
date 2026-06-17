"""Shared fixtures for chaos tests."""

from __future__ import annotations

import pytest

from core_engine.config import get_settings
from core_engine.services.message_log import clear_message_logs
from core_engine.tasks import celery_app


@pytest.fixture
def normal_send_settings(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("SHADOW_MODE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_logs():
    clear_message_logs()
    yield
    clear_message_logs()


@pytest.fixture
def celery_eager():
    """Run Celery tasks synchronously in-process."""
    previous_always_eager = celery_app.conf.task_always_eager
    previous_eager_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield celery_app
    finally:
        celery_app.conf.task_always_eager = previous_always_eager
        celery_app.conf.task_eager_propagates = previous_eager_propagates


@pytest.fixture
def bypass_consent(monkeypatch):
    monkeypatch.setattr(
        "core_engine.services.queue_manager.check_consent_or_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core_engine.services.message_dispatch.check_consent_or_log",
        lambda *args, **kwargs: None,
    )


SAMPLE_PAYLOAD = {
    "contact_id": 42,
    "campaign_id": 7,
    "platform": "telegram",
    "account_id": 2,
    "chat_identifier": "09121111111",
    "message_text": "chaos test message",
    "attempt_count": 0,
}
