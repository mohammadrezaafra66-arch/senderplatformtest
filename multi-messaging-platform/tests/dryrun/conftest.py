import pytest


@pytest.fixture(autouse=True)
def bypass_consent_when_no_db(monkeypatch):
    """Dry-run/shadow tests do not provide a DB session for consent checks."""

    def _skip_without_db(payload, *, db=None, source=""):
        if db is None:
            return None
        from core_engine.services.consent_gate import check_consent_or_log as original

        return original(payload, db=db, source=source)

    monkeypatch.setattr(
        "core_engine.services.queue_manager.check_consent_or_log",
        _skip_without_db,
    )
    monkeypatch.setattr(
        "core_engine.services.message_dispatch.check_consent_or_log",
        _skip_without_db,
    )
