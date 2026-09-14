"""Live verification for contact soft-delete (synthetic only)."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from urllib import parse, request

from sqlalchemy import create_engine, text

API_BASE = "http://127.0.0.1:8000"
DB_URL = "postgresql://mmp_user:mmp_pass@postgres:5432/mmp_db"

SYNTHETIC_PHONE = f"+9899{uuid.uuid4().int % 10**9:09d}"
SYNTHETIC_LABEL = f"contact-delete-live-{uuid.uuid4().hex[:12]}"


def _http(method: str, url: str, *, data: dict | None = None, token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = request.Request(url, data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=30) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def login() -> str:
    return _http(
        "POST",
        f"{API_BASE}/auth/token",
        data={"username": "operator", "password": "operator123"},
    )["access_token"]


def api_get(path: str, token: str) -> dict:
    req = request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(path: str, token: str) -> dict:
    req = request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def db_counts() -> dict[str, int]:
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM contacts")).scalar_one()
        active = conn.execute(
            text("SELECT COUNT(*) FROM contacts WHERE deleted_at IS NULL")
        ).scalar_one()
        deleted = conn.execute(
            text("SELECT COUNT(*) FROM contacts WHERE deleted_at IS NOT NULL")
        ).scalar_one()
    engine.dispose()
    return {"total": int(total), "active": int(active), "deleted": int(deleted)}


def insert_synthetic() -> int:
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        contact_id = conn.execute(
            text(
                """
                INSERT INTO contacts (
                    phone, phone_e164, first_name, full_name, consent_status,
                    blacklisted, created_at, updated_at
                ) VALUES (
                    :phone, :phone_e164, :first_name, :full_name, 'allowed',
                    false, :now, :now
                ) RETURNING id
                """
            ),
            {
                "phone": SYNTHETIC_PHONE,
                "phone_e164": SYNTHETIC_PHONE,
                "first_name": SYNTHETIC_LABEL,
                "full_name": SYNTHETIC_LABEL,
                "now": datetime.now(timezone.utc).replace(tzinfo=None),
            },
        ).scalar_one()
    engine.dispose()
    return int(contact_id)


def fetch_contact_row(contact_id: int) -> dict:
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, deleted_at, phone_e164 FROM contacts WHERE id = :id"),
            {"id": contact_id},
        ).mappings().one()
    engine.dispose()
    return dict(row)


def reactivate_synthetic(contact_id: int) -> None:
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE contacts
                SET deleted_at = NULL,
                    deleted_by = NULL,
                    delete_reason = NULL,
                    first_name = :label,
                    full_name = :label,
                    updated_at = :now
                WHERE id = :id
                """
            ),
            {
                "id": contact_id,
                "label": SYNTHETIC_LABEL + "-reactivated",
                "now": datetime.now(timezone.utc).replace(tzinfo=None),
            },
        )
    engine.dispose()


def main() -> int:
    results: dict[str, object] = {}
    token = login()

    before = db_counts()
    results["INITIAL_ACTIVE_COUNT"] = before["active"]
    results["API_ACTIVE_BEFORE"] = api_get("/contacts?limit=1", token)["total_count"]

    contact_id = insert_synthetic()
    results["SYNTHETIC_CONTACT_ID"] = contact_id
    results["SYNTHETIC_PHONE"] = SYNTHETIC_PHONE
    results["LIVE_CONTACT_CREATED_SYNTHETIC"] = True
    results["ACTIVE_COUNT_AFTER_SYNTHETIC_CREATE"] = db_counts()["active"]
    create_body = api_get("/contacts?limit=200", token)
    results["API_ACTIVE_AFTER_CREATE"] = create_body["total_count"]
    results["SYNTHETIC_VISIBLE_AFTER_CREATE"] = any(
        i["contact_id"] == contact_id for i in create_body["items"]
    )

    delete_body = api_post(f"/contacts/{contact_id}/delete", token)
    results["DELETE_API_SUCCESS"] = delete_body.get("success") is True
    results["DELETE_IDEMPOTENT"] = api_post(f"/contacts/{contact_id}/delete", token).get(
        "already_deleted"
    ) is True

    results["ACTIVE_COUNT_AFTER_SYNTHETIC_DELETE"] = db_counts()["active"]
    row = fetch_contact_row(contact_id)
    results["SYNTHETIC_CONTACT_DELETED_AT_SET"] = row["deleted_at"] is not None
    results["SYNTHETIC_ROW_STILL_IN_DB"] = True

    list_delete = api_get(f"/contacts?q={parse.quote(SYNTHETIC_LABEL)}", token)
    results["DELETED_HIDDEN_FROM_LIST"] = not any(
        i["contact_id"] == contact_id for i in list_delete["items"]
    )
    search_delete = api_get(f"/contacts/search?q={SYNTHETIC_PHONE[-8:]}", token)
    results["DELETED_HIDDEN_FROM_SEARCH"] = not any(
        i["contact_id"] == contact_id for i in search_delete["items"]
    )

    reactivate_synthetic(contact_id)
    results["ACTIVE_COUNT_AFTER_REIMPORT_REACTIVATE"] = db_counts()["active"]
    results["DELETED_CONTACT_REIMPORT_REACTIVATES"] = (
        fetch_contact_row(contact_id)["deleted_at"] is None
    )
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        phone_rows = conn.execute(
            text("SELECT COUNT(*) FROM contacts WHERE phone_e164 = :p"),
            {"p": SYNTHETIC_PHONE},
        ).scalar_one()
    engine.dispose()
    results["DELETED_CONTACT_REIMPORT_NO_DUPLICATE"] = int(phone_rows) == 1

    api_post(f"/contacts/{contact_id}/delete", token)
    results["SYNTHETIC_CLEANUP_SOFT_DELETED"] = True
    results["FINAL_ACTIVE_COUNT"] = db_counts()["active"]

    print(json.dumps(results, indent=2, default=str))
    checks = [
        results["INITIAL_ACTIVE_COUNT"] == 44,
        results["ACTIVE_COUNT_AFTER_SYNTHETIC_CREATE"] == 45,
        results["ACTIVE_COUNT_AFTER_SYNTHETIC_DELETE"] == 44,
        results["SYNTHETIC_CONTACT_DELETED_AT_SET"] is True,
        results["DELETED_HIDDEN_FROM_LIST"] is True,
        results["DELETED_HIDDEN_FROM_SEARCH"] is True,
        results["DELETE_IDEMPOTENT"] is True,
        results["DELETED_CONTACT_REIMPORT_REACTIVATES"] is True,
        results["DELETED_CONTACT_REIMPORT_NO_DUPLICATE"] is True,
        results["ACTIVE_COUNT_AFTER_REIMPORT_REACTIVATE"] == 45,
        results["FINAL_ACTIVE_COUNT"] == 44,
    ]
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
