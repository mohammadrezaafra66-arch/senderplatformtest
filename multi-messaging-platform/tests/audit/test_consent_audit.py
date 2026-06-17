import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.services.consent_service import record_opt_in, record_opt_out
from tests.consent.fake_db import ConsentFakeSession


@pytest.fixture
def audit_consent_db(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", Fernet.generate_key().decode())
    get_settings.cache_clear()
    session = ConsentFakeSession()
    session.seed_contact(contact_id=1)
    yield session
    get_settings.cache_clear()


def test_record_opt_out_writes_audit_entry(audit_consent_db):
    record_opt_out(audit_consent_db, 1, platform="whatsapp", username="operator")
    assert len(audit_consent_db.audit_logs) == 1
    row = audit_consent_db.audit_logs[0]
    assert row.action == "record_opt_out"
    assert row.username == "operator"
    assert row.resource_id == "1"


def test_record_opt_in_writes_audit_entry(audit_consent_db):
    record_opt_in(audit_consent_db, 1, platform="telegram", username="admin")
    assert len(audit_consent_db.audit_logs) == 1
    row = audit_consent_db.audit_logs[0]
    assert row.action == "record_opt_in"
    assert row.details["platform"] == "telegram"
