"""Tests for WhatsApp delivery audit and send guard."""

from core_engine.services.delivery_audit import record_whatsapp_delivery_audit
from core_engine.services.whatsapp_send_guard import (
    WHATSAPP_SEND_KILL_REDIS_KEY,
    whatsapp_sending_disabled_by_env,
)


def test_record_whatsapp_delivery_audit_persists(pg_session_factory):
    session = pg_session_factory()
    before = session.execute(
        __import__("sqlalchemy").text(
            "SELECT count(*) FROM audit_logs WHERE action = 'whatsapp_delivery'"
        )
    ).scalar()
    session.close()

    record_whatsapp_delivery_audit(
        source="script",
        account_id=248,
        recipient="989122270261",
        message_id="script-test-1",
        message_text="TEST123 simple ascii",
        success=True,
        status="delivered",
        platform_message_id="wa-web-test",
    )

    session = pg_session_factory()
    after = session.execute(
        __import__("sqlalchemy").text(
            "SELECT count(*) FROM audit_logs WHERE action = 'whatsapp_delivery'"
        )
    ).scalar()
    row = session.execute(
        __import__("sqlalchemy").text(
            "SELECT details FROM audit_logs WHERE action = 'whatsapp_delivery' ORDER BY id DESC LIMIT 1"
        )
    ).scalar()
    session.close()

    assert after == before + 1
    assert row is not None
    assert "script" in str(row)
    assert "989122270261" in str(row)
    assert "TEST123 simple ascii" in str(row)


def test_record_worker_whatsapp_delivery_persists(pg_session_factory):
    from workers.payloads import WorkerPayload, WorkerResult

    session = pg_session_factory()
    before = session.execute(
        __import__("sqlalchemy").text(
            "SELECT count(*) FROM audit_logs WHERE action = 'whatsapp_delivery'"
        )
    ).scalar()
    session.close()

    payload = WorkerPayload.model_validate(
        {
            "message_id": "ops-test-abc123",
            "campaign_id": "ops-test-248",
            "contact_id": "ops-test-abc123",
            "account_id": 248,
            "platform": "whatsapp",
            "recipient": "989122270261",
            "recipient_type": "phone_number",
            "message_text": "پیام تست audit worker",
            "dedupe_key": "ops-test-248-abc123",
            "metadata": {"source": "operational_send_test", "test_id": "abc123"},
        }
    )
    result = WorkerResult(
        success=True,
        status="delivered",
        platform_message_id="wa-web-test-worker",
    )

    from core_engine.services.delivery_audit import record_worker_whatsapp_delivery

    record_worker_whatsapp_delivery(payload, result)

    session = pg_session_factory()
    after = session.execute(
        __import__("sqlalchemy").text(
            "SELECT count(*) FROM audit_logs WHERE action = 'whatsapp_delivery'"
        )
    ).scalar()
    row = session.execute(
        __import__("sqlalchemy").text(
            "SELECT details FROM audit_logs WHERE action = 'whatsapp_delivery' ORDER BY id DESC LIMIT 1"
        )
    ).scalar()
    session.close()

    assert after == before + 1
    assert "worker" in str(row)
    assert "ui" in str(row)
    assert "پیام تست audit worker" in str(row)


def test_whatsapp_sending_disabled_by_env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_SENDING_DISABLED", "true")
    from core_engine.config import get_settings

    get_settings.cache_clear()
    assert whatsapp_sending_disabled_by_env() is True
    get_settings.cache_clear()
    monkeypatch.delenv("WHATSAPP_SENDING_DISABLED", raising=False)


def test_whatsapp_send_kill_switch_redis_key():
    assert WHATSAPP_SEND_KILL_REDIS_KEY == "system:whatsapp_send_disabled"
