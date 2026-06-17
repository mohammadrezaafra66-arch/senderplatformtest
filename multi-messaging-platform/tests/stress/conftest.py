"""Shared fixtures for stress tests."""

from __future__ import annotations

import os

import pytest

from core_engine.config import get_settings
from core_engine.services.message_log import clear_message_logs


STRESS_MESSAGE_COUNT = int(os.environ.get("STRESS_MESSAGE_COUNT", "1000"))
STRESS_MAX_SECONDS = float(os.environ.get("STRESS_MAX_SECONDS", "10"))


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
def bypass_consent(monkeypatch):
    monkeypatch.setattr(
        "core_engine.services.queue_manager.check_consent_or_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core_engine.services.message_dispatch.check_consent_or_log",
        lambda *args, **kwargs: None,
    )
