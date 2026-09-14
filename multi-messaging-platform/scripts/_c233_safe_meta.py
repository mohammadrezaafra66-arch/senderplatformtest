"""Secret-safe metadata for Campaign 233 Base64 forensics. No plaintext/secrets."""
from __future__ import annotations

import base64
import re
from sqlalchemy import text
from core_engine.database import SessionLocal

db = SessionLocal()
try:
    rows = db.execute(
        text(
            """
            SELECT ma.id, ma.message_id, ma.attempt_no, ma.status::text AS status,
                   ma.error_code, ma.error_message, ma.created_at, ma.started_at,
                   m.campaign_id, m.account_id, m.contact_id
            FROM message_attempts ma
            JOIN messages m ON m.id = ma.message_id
            WHERE m.campaign_id = 233
            ORDER BY ma.id
            """
        )
    ).mappings().all()
    print("ATTEMPT_COUNT", len(rows))
    for r in rows:
        print(
            "ATTEMPT",
            dict(
                id=r["id"],
                message_id=r["message_id"],
                attempt_no=r["attempt_no"],
                status=r["status"],
                error_code=r["error_code"],
                error_message=r["error_message"],
                created_at=str(r["created_at"]),
                started_at=str(r["started_at"]),
                campaign_id=r["campaign_id"],
                account_id=r["account_id"],
                contact_id=r["contact_id"],
            ),
        )

    for aid in (2269, 79):
        sess = db.execute(
            text(
                """
                SELECT id, session_type::text AS session_type,
                       session_status::text AS session_status,
                       key_version, identity_guid IS NOT NULL AS has_identity_guid,
                       login_attempt_id IS NOT NULL AS has_login_attempt_id,
                       ciphertext IS NULL AS ct_is_null,
                       CASE WHEN ciphertext IS NULL THEN 0 ELSE length(ciphertext) END AS ct_len,
                       created_at, updated_at
                FROM channel_sessions
                WHERE account_id = :aid
                  AND session_type = 'RUBIKA_SESSION'
                ORDER BY id DESC
                LIMIT 3
                """
            ),
            {"aid": aid},
        ).mappings().all()
        print("ACCOUNT_SESSIONS", aid, "count_shown", len(sess))
        for s in sess:
            print("META", aid, dict(s))

        # Outer ciphertext format only: length/mod4/charset — never print content
        row = db.execute(
            text(
                """
                SELECT id, ciphertext
                FROM channel_sessions
                WHERE account_id = :aid AND session_type = 'RUBIKA_SESSION'
                ORDER BY id DESC LIMIT 1
                """
            ),
            {"aid": aid},
        ).mappings().first()
        if not row or row["ciphertext"] is None:
            print("OUTER", aid, "MISSING")
            continue
        ct = row["ciphertext"]
        L = len(ct)
        # urlsafe charset used by encode_ciphertext_blob
        urlsafe = bool(re.fullmatch(r"[A-Za-z0-9_=-]*", ct))
        # also allow standard alphabet
        std = bool(re.fullmatch(r"[A-Za-z0-9+/_=-]*", ct))
        mod4 = L % 4
        structurally = urlsafe and mod4 != 1 and L > 0
        outer_ok = False
        outer_err = None
        try:
            base64.urlsafe_b64decode(ct.encode("ascii"))
            outer_ok = True
        except Exception as e:  # noqa: BLE001
            outer_err = f"{type(e).__name__}:{str(e)[:120]}"
        print(
            "OUTER",
            aid,
            {
                "session_id": row["id"],
                "ct_len": L,
                "ct_mod4": mod4,
                "charset_urlsafe": urlsafe,
                "charset_std_or_urlsafe": std,
                "structurally_valid_urlsafe": structurally,
                "urlsafe_b64decode_ok": outer_ok,
                "urlsafe_b64decode_err": outer_err,
            },
        )
finally:
    db.close()
