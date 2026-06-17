from core_engine.services.audit_service import list_audit_logs, record_audit
from tests.audit.fake_db import AuditFakeSession


def test_record_audit_persists_entry():
    db = AuditFakeSession()
    entry = record_audit(
        db,
        "admin",
        "set_kill_switch",
        "controls",
        "kill_switch",
        {"enabled": True},
    )

    assert entry.id == 1
    assert entry.username == "admin"
    assert entry.action == "set_kill_switch"
    assert entry.resource_type == "controls"
    assert entry.resource_id == "kill_switch"
    assert entry.details == {"enabled": True}
    assert entry.timestamp is not None
    assert len(db.logs) == 1


def test_list_audit_logs_returns_latest_first():
    db = AuditFakeSession()
    record_audit(db, "admin", "action_a", "resource", "1")
    record_audit(db, "operator", "action_b", "resource", "2")

    rows = list_audit_logs(db, limit=10)
    assert len(rows) == 2
    assert rows[0].action == "action_b"
    assert rows[1].action == "action_a"
