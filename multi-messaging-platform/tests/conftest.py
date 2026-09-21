import os

from cryptography.fernet import Fernet

# Ensure required secrets exist before importing the FastAPI app in tests.
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-change-me")
os.environ.setdefault("SESSION_SECRET", Fernet.generate_key().decode())

# Rewrite DATABASE_URL to mmp_pytest_* before SessionLocal/engine/app import.
from tests.isolation import configure_pytest_database_url

configure_pytest_database_url()

import pytest
from fastapi.testclient import TestClient

from core_engine.config import get_settings
from core_engine.main import app


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _rubika_activation_gate_off_except_trust_tests(monkeypatch, request):
    """Isolate pool/rate/session suites from the manager-confirm gate.

    Production default remains required. Activation + L3 login tests keep it on.
    """
    path = str(getattr(request, "fspath", "") or "").replace("\\", "/")
    if "test_rubika_account_activation" in path:
        return
    if "test_rubika_l3_login_state_machine" in path:
        return
    if "test_campaign_automatic_sender_assignment" in path:
        return
    monkeypatch.setenv("RUBIKA_MANAGER_ACTIVATION_REQUIRED", "false")
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)
