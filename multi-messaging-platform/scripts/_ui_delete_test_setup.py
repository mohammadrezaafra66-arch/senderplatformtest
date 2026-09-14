"""Create synthetic contact for UI delete verification."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from urllib import parse, request

from sqlalchemy import create_engine, text

DB_URL = "postgresql://mmp_user:mmp_pass@postgres:5432/mmp_db"
API_BASE = "http://127.0.0.1:8000"

label = f"ui-delete-test-{uuid.uuid4().hex[:8]}"
phone = f"+9899{uuid.uuid4().int % 10**9:09d}"
now = datetime.now(timezone.utc).replace(tzinfo=None)

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
            "phone": phone,
            "phone_e164": phone,
            "first_name": label,
            "full_name": label,
            "now": now,
        },
    ).scalar_one()
engine.dispose()

body = parse.urlencode({"username": "operator", "password": "operator123"}).encode()
token = json.loads(
    request.urlopen(
        request.Request(
            f"{API_BASE}/auth/token",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    ).read()
)["access_token"]
listing = json.loads(
    request.urlopen(
        request.Request(
            f"{API_BASE}/contacts",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).read()
)

print(
    json.dumps(
        {
            "contact_id": int(contact_id),
            "label": label,
            "phone": phone,
            "active_count": listing["total_count"],
        }
    )
)
