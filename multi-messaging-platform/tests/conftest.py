import os

from cryptography.fernet import Fernet

# Ensure required secrets exist before importing the FastAPI app in tests.
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-change-me")
os.environ.setdefault("SESSION_SECRET", Fernet.generate_key().decode())

import pytest
from fastapi.testclient import TestClient

from core_engine.config import get_settings
from core_engine.main import app


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)
