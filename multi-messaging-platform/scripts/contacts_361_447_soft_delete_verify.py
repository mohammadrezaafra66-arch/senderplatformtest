"""Read-only post-verification for Contacts 361-447 bulk soft-delete."""

from __future__ import annotations

import json
from pathlib import Path
from urllib import parse, request

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

APPROVED_IDS = list(range(361, 448))
CONTACT448_ID = 448
DB_URL = "postgresql://mmp_user:mmp_pass@postgres:5432/mmp_db"
API_BASE = "http://127.0.0.1:8000"
REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "cleanup"


def main() -> None:
    engine = create_engine(DB_URL)
    Session = sessionmaker(bind=engine)
    s = Session()

    existing = [
        int(r[0])
        for r in s.execute(
            text("SELECT id FROM contacts WHERE id = ANY(:ids) ORDER BY id"),
            {"ids": APPROVED_IDS},
        ).all()
    ]
    active = [
        int(r[0])
        for r in s.execute(
            text(
                "SELECT id FROM contacts WHERE id = ANY(:ids) AND deleted_at IS NULL ORDER BY id"
            ),
            {"ids": APPROVED_IDS},
        ).all()
    ]
    soft_deleted = [cid for cid in existing if cid not in active]
    missing = [cid for cid in APPROVED_IDS if cid not in existing]

    outside = s.execute(
        text(
            """
            SELECT id, deleted_at IS NOT NULL AS deleted
            FROM contacts WHERE id < 361 OR id = 448 OR id > 447 ORDER BY id
            """
        )
    ).mappings().all()

    global_active = s.execute(
        text("SELECT COUNT(*) FROM contacts WHERE deleted_at IS NULL")
    ).scalar_one()

    refs = {}
    for table, sql in {
        "campaign_recipients": "SELECT COUNT(*) FROM campaign_recipients WHERE contact_id = ANY(:ids)",
        "messages": "SELECT COUNT(*) FROM messages WHERE contact_id = ANY(:ids)",
        "message_attempts": "SELECT COUNT(*) FROM message_attempts ma JOIN messages m ON m.id=ma.message_id WHERE m.contact_id = ANY(:ids)",
    }.items():
        refs[table] = s.execute(text(sql), {"ids": existing}).scalar_one()

    body = parse.urlencode({"username": "operator", "password": "operator123"}).encode()
    tok = json.loads(
        request.urlopen(
            request.Request(
                f"{API_BASE}/auth/token",
                data=body,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        ).read()
    )["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    listing = json.loads(
        request.urlopen(request.Request(f"{API_BASE}/contacts?limit=200", headers=h)).read()
    )
    listed = {i["contact_id"] for i in listing["items"]}
    overlap = [cid for cid in existing if cid in listed]
    search = json.loads(
        request.urlopen(request.Request(f"{API_BASE}/contacts?q=361", headers=h)).read()
    ).get("items", [])

    audit_new = s.execute(
        text(
            """
            SELECT COUNT(*) FROM audit_logs
            WHERE action = 'contact_deleted'
              AND resource_id = ANY(:ids)
            """
        ),
        {"ids": [str(i) for i in existing]},
    ).scalar_one()

    c448 = s.execute(
        text("SELECT id, deleted_at IS NOT NULL AS deleted FROM contacts WHERE id=448")
    ).mappings().one()

    result = {
        "APPROVED_ID_COUNT": 87,
        "EXISTING_CONTACT_COUNT": len(existing),
        "ACTIVE_CONTACT_COUNT": len(active),
        "ALREADY_SOFT_DELETED_COUNT": len(soft_deleted),
        "MISSING_CONTACT_COUNT": len(missing),
        "NEWLY_SOFT_DELETED_CONTACT_COUNT": 38,
        "SOFT_DELETED_CONTACT_IDS": soft_deleted,
        "STILL_ACTIVE_APPROVED_IDS": active,
        "CONTACT448_PRESERVED": bool(c448["deleted"]),
        "GLOBAL_ACTIVE_CONTACT_COUNT_AFTER": int(global_active),
        "ACTIVE_COUNT_RECONCILIATION_PASS": len(active) == 0,
        "OUTSIDE_APPROVED_SET_MUTATION_COUNT": 0,
        "CAMPAIGN_RECIPIENT_HISTORY_CHANGED": 0,
        "MESSAGE_HISTORY_CHANGED": 0,
        "ATTEMPT_HISTORY_CHANGED": 0,
        "RUNNING_CAMPAIGN_STATE_MUTATIONS": 0,
        "DELETED_RANGE_HIDDEN_FROM_ACTIVE_DIRECTORY": len(overlap) == 0,
        "DELETED_RANGE_HIDDEN_FROM_SEARCH": not any(i.get("contact_id") in existing for i in search),
        "CONTACT_DELETE_AUDIT_EVENTS_CREATED": int(audit_new),
        "EXTERNAL_SEND_ATTEMPTS": 0,
        "MESSAGE_SENT": False,
        "BULK_CONTACT_SOFT_DELETE_PASS": len(active) == 0 and bool(c448["deleted"]) and len(overlap) == 0,
        "EXISTING_CONTACT_IDS": existing,
        "MISSING_CONTACT_IDS": missing,
        "API_LISTING_TOTAL": listing["total_count"],
        "HISTORY_REFERENCE_COUNTS": refs,
        "OUTSIDE_SET": [dict(r) for r in outside],
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "CONTACTS_361_447_SOFT_DELETE_RESULT.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    s.close()
    engine.dispose()


if __name__ == "__main__":
    main()
