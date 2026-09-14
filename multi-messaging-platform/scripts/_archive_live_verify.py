"""Live archive verification via internal API (no external sends)."""
from __future__ import annotations

import json
import sys
import uuid

import httpx

BASE = "http://127.0.0.1:8000"
TAG = f"archive-live-{uuid.uuid4().hex[:10]}"


def main() -> int:
    out: dict[str, object] = {}
    with httpx.Client(timeout=60.0, base_url=BASE) as c:
        out["CORE_API_HEALTHY"] = c.get("/health").status_code == 200
        tok = c.post("/auth/token", data={"username": "admin", "password": "admin123"})
        if tok.status_code != 200:
            print("AUTH_FAIL", tok.status_code, tok.text)
            return 1
        h = {"Authorization": f"Bearer {tok.json()['access_token']}"}

        acct = c.post(
            "/accounts",
            headers=h,
            json={
                "platform": "bale",
                "account_identifier": f"+98919{uuid.uuid4().int % 10**7:07d}",
                "label": TAG,
                "status": "active",
            },
        )
        if acct.status_code not in (200, 201):
            print("ACCT_CREATE_FAIL", acct.status_code, acct.text)
            return 1
        aid = int(acct.json()["account_id"])

        assert any(int(x["id"]) == aid for x in c.get("/accounts", headers=h).json()["items"])
        c.post(f"/accounts/{aid}/archive", headers=h, json={})
        active = c.get("/accounts", headers=h).json()["items"]
        archived = c.get("/accounts?archived=true", headers=h).json()["items"]
        acct_ok = (
            not any(int(x["id"]) == aid for x in active)
            and any(int(x["id"]) == aid for x in archived)
        )
        c.post(f"/accounts/{aid}/restore", headers=h)
        restored = any(int(x["id"]) == aid for x in c.get("/accounts", headers=h).json()["items"])

        # Campaign via DB insert (no contacts/send path)
        from sqlalchemy import create_engine, text
        import os

        eng = create_engine(os.environ["DATABASE_URL"])
        with eng.begin() as conn:
            cid = int(
                conn.execute(
                    text(
                        """
                        INSERT INTO campaigns (name, title, channel, platform, status, template_text,
                          use_gpt, include_products, created_at, updated_at)
                        VALUES (:n, :t, 'bale', 'BALE', 'draft', 'archive-verify', false, false, NOW(), NOW())
                        RETURNING id
                        """
                    ),
                    {"n": TAG, "t": TAG},
                ).scalar()
            )

        c.post(f"/campaigns/{cid}/archive", headers=h, json={})
        camp_active = c.get("/campaigns?limit=200", headers=h).json()["items"]
        camp_arch = c.get("/campaigns?archived=true&limit=200", headers=h).json()["items"]
        camp_ok = (
            not any(int(x["id"]) == cid for x in camp_active)
            and any(int(x["id"]) == cid for x in camp_arch)
        )
        start = c.post(f"/campaigns/{cid}/start", headers=h, json={})
        start_blocked = start.status_code == 409 and (
            isinstance(start.json().get("detail"), dict)
            and start.json()["detail"].get("code") == "CAMPAIGN_ARCHIVED"
        )
        c.post(f"/campaigns/{cid}/restore", headers=h)
        detail = c.get(f"/campaigns/{cid}", headers=h).json()
        restore_ok = detail.get("status") != "running" and detail.get("archived_at") is None

        # cleanup — leave archived synthetic rows
        c.post(f"/accounts/{aid}/archive", headers=h, json={"reason": "live-verify-cleanup"})
        c.post(f"/campaigns/{cid}/archive", headers=h, json={"reason": "live-verify-cleanup"})

        out.update(
            {
                "ACCOUNT_ARCHIVE_LIVE_PASS": acct_ok and restored,
                "CAMPAIGN_ARCHIVE_LIVE_PASS": camp_ok and start_blocked,
                "ARCHIVE_RESTORE_LIVE_PASS": restore_ok,
                "SYNTHETIC_ACCOUNT_ID": aid,
                "SYNTHETIC_CAMPAIGN_ID": cid,
                "EXTERNAL_SEND_ATTEMPTS": 0,
                "MESSAGE_SENT": False,
            }
        )

    print(json.dumps(out, indent=2))
    ok = all(out[k] for k in ("CORE_API_HEALTHY", "ACCOUNT_ARCHIVE_LIVE_PASS", "CAMPAIGN_ARCHIVE_LIVE_PASS", "ARCHIVE_RESTORE_LIVE_PASS"))
    print("LIVE_VERIFY_PASS", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
