import pytest

from core_engine.config import get_settings
from core_engine.models import SendStatus
from core_engine.services.consent_service import (
    has_opted_in,
    record_opt_in,
    record_opt_out,
)
from core_engine.services.message_log import clear_message_logs, get_message_logs
from core_engine.services.queue_manager import enqueue_message
from tests.consent.fake_db import ConsentFakeSession


@pytest.fixture
def consent_db(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("SHADOW_MODE", "false")
    get_settings.cache_clear()

    session = ConsentFakeSession()
    session.seed_contact(contact_id=1, blacklisted=False)
    yield session
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_logs():
    clear_message_logs()
    yield
    clear_message_logs()


def test_has_opted_in_defaults_true_without_events(consent_db):
    assert has_opted_in(consent_db, 1, "whatsapp") is True


def test_record_opt_out_makes_has_opted_in_false(consent_db):
    record_opt_out(consent_db, 1, platform="whatsapp", reason="user request")
    assert has_opted_in(consent_db, 1, "whatsapp") is False
    from core_engine.models import Contact

    assert consent_db.get(Contact, 1).blacklisted is True


def test_record_opt_in_after_opt_out_restores_consent(consent_db):
    record_opt_out(consent_db, 1, platform="whatsapp")
    record_opt_in(consent_db, 1, platform="whatsapp")
    assert has_opted_in(consent_db, 1, "whatsapp") is True
    from core_engine.models import Contact

    assert consent_db.get(Contact, 1).blacklisted is False


def test_blacklisted_contact_returns_false_even_with_opt_in_event(consent_db):
    consent_db.contacts[1].blacklisted = True
    record_opt_in(consent_db, 1, platform="whatsapp")
    consent_db.contacts[1].blacklisted = True
    assert has_opted_in(consent_db, 1, "whatsapp") is False


def test_enqueue_skips_opted_out_contact(consent_db):
    record_opt_out(consent_db, 1, platform="whatsapp")

    payload = {
        "contact_id": 1,
        "campaign_id": 5,
        "platform": "whatsapp",
        "chat_identifier": "09120000000",
        "message_text": "blocked message",
    }
    result = enqueue_message(payload, db=consent_db)

    assert result["enqueued"] is False
    assert result["status"] == SendStatus.OPTED_OUT.value
    logs = get_message_logs()
    assert len(logs) == 1
    assert logs[0].status == SendStatus.OPTED_OUT.value


def test_enqueue_skips_blacklisted_contact(consent_db):
    consent_db.contacts[1].blacklisted = True

    payload = {
        "contact_id": 1,
        "campaign_id": 5,
        "platform": "whatsapp",
        "chat_identifier": "09120000000",
        "message_text": "blocked message",
    }
    result = enqueue_message(payload, db=consent_db)

    assert result["enqueued"] is False
    assert result["status"] == SendStatus.BLACKLISTED.value
    assert get_message_logs()[0].status == SendStatus.BLACKLISTED.value
