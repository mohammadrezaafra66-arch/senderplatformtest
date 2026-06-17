import pytest
from sqlalchemy.exc import OperationalError

from core_engine.models import SendStatus
from core_engine.services.consent_gate import check_consent_or_log
from core_engine.services.consent_service import record_opt_out
from core_engine.services.message_log import get_message_logs
from tests.consent.fake_db import ConsentFakeSession


@pytest.mark.chaos
def test_db_failure_during_consent_check_is_controlled(monkeypatch):
    class FailingSession:
        def query(self, *_args, **_kwargs):
            raise OperationalError("simulated db down", {}, Exception())

    monkeypatch.setattr(
        "core_engine.services.consent_gate.SessionLocal",
        lambda: FailingSession(),
    )

    result = check_consent_or_log(
        {
            "contact_id": 1,
            "campaign_id": 1,
            "platform": "whatsapp",
            "chat_identifier": "09120000000",
            "message_text": "db chaos",
        },
        source="chaos_test",
    )

    assert result is not None
    assert result["enqueued"] is False
    assert result["status"] == SendStatus.FAILED_RETRYABLE.value
    assert result["error"] == "consent_check_unavailable"

    logs = get_message_logs()
    assert len(logs) == 1
    assert logs[0].status != SendStatus.BLACKLISTED.value


@pytest.mark.chaos
def test_db_failure_during_opt_out_rolls_back_and_recovers():
    session = ConsentFakeSession()
    session.seed_contact(contact_id=1)
    calls = {"count": 0}
    original_get = session.get

    def flaky_get(model, contact_id):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("simulated write failure", {}, Exception())
        return original_get(model, contact_id)

    session.get = flaky_get  # type: ignore[method-assign]

    with pytest.raises(OperationalError):
        record_opt_out(session, 1, platform="whatsapp")

    assert len(session.events) == 0
    assert session.contacts[1].blacklisted is False

    event = record_opt_out(session, 1, platform="whatsapp")
    assert event.opted_in is False
    assert session.contacts[1].blacklisted is True
