"""Re-import Contact 448 through real import API (synthetic fixture only).

Safety contract:
- Operates only on synthetic Contact ID 448 / phone +9899180311611
- Uses POST /imports/contacts/preview + /imports/contacts/commit only
- Never runs SQL UPDATE/DELETE
- Aborts unless the reactivated Contact ID is exactly 448
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from openpyxl import Workbook
from sqlalchemy import create_engine, text

API = "http://127.0.0.1:8000"
DB_URL = "postgresql://mmp_user:mmp_pass@postgres:5432/mmp_db"
PHONE_E164 = "+9899180311611"
CONTACT_ID = 448
OUT = Path("/tmp/contact448_reimport.xlsx")


def login(client: httpx.Client) -> str:
    r = client.post(
        "/auth/token",
        data={"username": "operator", "password": "operator123"},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def assert_fixture_identity() -> None:
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, phone_e164, deleted_at IS NOT NULL AS deleted
                FROM contacts
                WHERE id = :id
                """
            ),
            {"id": CONTACT_ID},
        ).mappings().one_or_none()
        phone_rows = conn.execute(
            text("SELECT id, deleted_at IS NOT NULL AS deleted FROM contacts WHERE phone_e164 = :p"),
            {"p": PHONE_E164},
        ).mappings().all()
    engine.dispose()

    if row is None:
        raise SystemExit(json.dumps({"error": "CONTACT448_MISSING"}))
    if row["phone_e164"] != PHONE_E164:
        raise SystemExit(
            json.dumps(
                {
                    "error": "CONTACT448_PHONE_MISMATCH",
                    "expected": PHONE_E164,
                    "actual": row["phone_e164"],
                }
            )
        )
    if len(phone_rows) != 1 or int(phone_rows[0]["id"]) != CONTACT_ID:
        raise SystemExit(
            json.dumps(
                {
                    "error": "CANONICAL_PHONE_AMBIGUOUS_OR_WRONG_ID",
                    "rows": [dict(r) for r in phone_rows],
                }
            )
        )
    if not row["deleted"]:
        raise SystemExit(json.dumps({"error": "CONTACT448_NOT_SOFT_DELETED"}))


def assert_reactivated_exactly_448() -> dict:
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, phone_e164, deleted_at
                FROM contacts
                WHERE phone_e164 = :p
                ORDER BY id
                """
            ),
            {"p": PHONE_E164},
        ).mappings().all()
        contact = conn.execute(
            text(
                """
                SELECT id, deleted_at, first_name
                FROM contacts
                WHERE id = :id
                """
            ),
            {"id": CONTACT_ID},
        ).mappings().one()
    engine.dispose()

    if len(rows) != 1:
        raise SystemExit(
            json.dumps({"error": "DUPLICATE_OR_MISSING_CANONICAL", "rows": [dict(r) for r in rows]})
        )
    if int(rows[0]["id"]) != CONTACT_ID:
        raise SystemExit(
            json.dumps(
                {
                    "error": "WRONG_CONTACT_MUTATED",
                    "expected_id": CONTACT_ID,
                    "actual_id": int(rows[0]["id"]),
                }
            )
        )
    if contact["deleted_at"] is not None:
        raise SystemExit(json.dumps({"error": "CONTACT448_STILL_DELETED", "row": dict(contact)}))
    return {"id": int(contact["id"]), "first_name": contact["first_name"]}


def main() -> int:
    assert_fixture_identity()

    wb = Workbook()
    ws = wb.active
    ws.append(["phone", "first_name"])
    ws.append([PHONE_E164, "contact448-reimport-live"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)

    with httpx.Client(base_url=API, timeout=60.0) as client:
        token = login(client)
        headers = {"Authorization": f"Bearer {token}"}

        with OUT.open("rb") as handle:
            preview_resp = client.post(
                "/imports/contacts/preview",
                headers=headers,
                files={
                    "file": (
                        "contact448_reimport.xlsx",
                        handle,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
        preview_body = preview_resp.json()
        if preview_resp.status_code != 200:
            print(json.dumps({"step": "preview", "status": preview_resp.status_code, "body": preview_body}))
            return 1

        commit_payload = {
            "file_path": preview_body["file_path"],
            "original_file_name": preview_body["original_file_name"],
            "stored_file_name": preview_body["stored_file_name"],
            "sheet_name": preview_body.get("sheet_name"),
            "uploaded_by": "contact448-live-verify",
        }
        commit_resp = client.post(
            "/imports/contacts/commit",
            headers=headers,
            json=commit_payload,
        )
        commit_body = commit_resp.json() if commit_resp.content else {}
        if commit_resp.status_code != 200 or commit_body.get("status") != "committed":
            print(
                json.dumps(
                    {
                        "step": "commit",
                        "status": commit_resp.status_code,
                        "body": commit_body,
                    },
                    ensure_ascii=False,
                )
            )
            return 1

        restored = assert_reactivated_exactly_448()
        print(
            json.dumps(
                {
                    "preview_status": preview_resp.status_code,
                    "preview_status_field": preview_body.get("status"),
                    "commit_status": commit_resp.status_code,
                    "commit_body": commit_body,
                    "REACTIVATED_CONTACT_ID": restored["id"],
                    "CONTACT448_REACTIVATED": restored["id"] == CONTACT_ID,
                },
                ensure_ascii=False,
            )
        )
        return 0 if restored["id"] == CONTACT_ID else 1


if __name__ == "__main__":
    raise SystemExit(main())
