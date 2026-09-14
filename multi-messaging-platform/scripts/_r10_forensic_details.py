"""Extra forensic details — READ ONLY."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal

DIFF = Path("/tmp/r10_forensic_out/R10_R0_DIFF.json")
OUT = Path("/tmp/r10_forensic_out/R10_FORENSIC_DETAILS.json")


def parse_copy(path: str):
    rows = []
    in_copy = False
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("COPY "):
            in_copy = True
            continue
        if in_copy and line.startswith("\\."):
            break
        if in_copy and line and not line.startswith("--"):
            rows.append(line.split("\t"))
    return rows


def main() -> None:
    d = json.loads(DIFF.read_text(encoding="utf-8"))
    sess_sql = Path("/tmp/r0_extract/r0_sessions.sql").read_text(encoding="utf-8", errors="replace")
    copy_header = next(line for line in sess_sql.splitlines() if line.startswith("COPY "))
    r0_sess = parse_copy("/tmp/r0_extract/r0_sessions.sql")
    r0_sess_12_79 = []
    for row in r0_sess:
        if row[1] in ("12", "79"):
            long_fields = []
            for i, c in enumerate(row):
                if len(c) > 50:
                    long_fields.append(
                        {
                            "idx": i,
                            "len": len(c),
                            "sha16": hashlib.sha256(c.encode()).hexdigest()[:16],
                        }
                    )
            r0_sess_12_79.append(
                {
                    "id": int(row[0]),
                    "account_id": int(row[1]),
                    "created_at": row[-2] if len(row) > 2 else None,
                    "updated_at": row[-1],
                    "long_fields": long_fields,
                }
            )

    db = SessionLocal()
    details: dict = {
        "r0_sessions_copy_header": copy_header[:400],
        "r0_sessions_12_79": r0_sess_12_79,
    }
    try:
        live_sess = db.execute(
            text(
                """
                SELECT id, account_id, session_type::text, created_at, updated_at,
                       length(COALESCE(ciphertext, '')) AS cipher_len
                FROM channel_sessions
                WHERE account_id IN (12, 79)
                ORDER BY account_id, id
                """
            )
        ).fetchall()
        live_rows = []
        for r in live_sess:
            row = dict(r._mapping)
            # hash ciphertext in Python to avoid pgcrypto dependency
            cipher = db.execute(
                text("SELECT ciphertext FROM channel_sessions WHERE id = :id"),
                {"id": r.id},
            ).scalar()
            if cipher:
                row["cipher_sha16"] = hashlib.sha256(cipher.encode()).hexdigest()[:16]
            else:
                row["cipher_sha16"] = None
            row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
            row["updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
            live_rows.append(row)
        details["live_sessions_12_79"] = live_rows

        contacts = db.execute(
            text(
                """
                SELECT id, first_name, consent_status, campaign_id, created_at, updated_at,
                       (phone_e164 IS NOT NULL) AS has_e164
                FROM contacts WHERE id IN (2, 92)
                """
            )
        ).fetchall()
        details["authorized_contacts"] = [
            {
                **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r._mapping).items()}
            }
            for r in contacts
        ]

        camps = d["new_campaigns_detail"]
        details["campaigns_by_day"] = dict(Counter((c["created_at"] or "")[:10] for c in camps))
        prefix = Counter()
        for c in camps:
            n = c["name"]
            if n.startswith("p6-"):
                prefix["p6-*"] += 1
            elif n.startswith("r22-"):
                prefix["r22-*"] += 1
            else:
                prefix[n] += 1
        details["campaigns_by_prefix"] = dict(prefix)
        details["campaigns_by_status"] = dict(Counter(c["status"] for c in camps))
        details["new_campaign_ids"] = [c["id"] for c in camps]
        details["new_campaign_names"] = [{"id": c["id"], "name": c["name"], "status": c["status"]} for c in camps]

        atts = db.execute(
            text(
                """
                SELECT id, message_id, attempt_no, status::text, platform_message_id,
                       error_code, created_at
                FROM message_attempts
                WHERE platform_message_id IS NOT NULL
                ORDER BY id
                """
            )
        ).fetchall()
        att_out = []
        for a in atts:
            m = db.execute(
                text(
                    "SELECT id, campaign_id, account_id, contact_id, created_at FROM messages WHERE id = :id"
                ),
                {"id": a.message_id},
            ).first()
            att_out.append(
                {
                    "attempt": {
                        k: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for k, v in dict(a._mapping).items()
                    },
                    "message": (
                        {
                            k: (v.isoformat() if hasattr(v, "isoformat") else v)
                            for k, v in dict(m._mapping).items()
                        }
                        if m
                        else None
                    ),
                }
            )
        details["attempts_with_platform_message_id"] = att_out

        live195 = db.execute(
            text(
                """
                SELECT id, phase, is_active, start_hour, end_hour, max_per_hour,
                       created_at, updated_at
                FROM rubika_sender_schedules WHERE id = 195
                """
            )
        ).first()
        details["schedule_195_live"] = {
            k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in dict(live195._mapping).items()
        }

        new_pool_ids = set(d["diff_by_table"]["rubika_account_pool"]["ids_in_live_not_r0"])
        pool = db.execute(
            text(
                "SELECT id, account_id, phase, priority, created_at FROM rubika_account_pool ORDER BY id"
            )
        ).fetchall()
        new_pool = []
        for p in pool:
            if p.id in new_pool_ids:
                new_pool.append(
                    {
                        k: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for k, v in dict(p._mapping).items()
                    }
                )
        details["new_pool_rows"] = new_pool
        details["new_pool_for_12_79"] = [p for p in new_pool if p["account_id"] in (12, 79)]

        new_camp_ids = d["diff_by_table"]["campaigns"]["ids_in_live_not_r0"]
        ca = db.execute(
            text(
                "SELECT campaign_id, account_id FROM campaign_accounts WHERE campaign_id = ANY(:ids)"
            ),
            {"ids": new_camp_ids},
        ).fetchall()
        acct_set = {r.account_id for r in ca}
        details["new_campaign_linked_account_ids_count"] = len(acct_set)
        details["new_campaigns_using_12_or_79"] = [
            {"campaign_id": r.campaign_id, "account_id": r.account_id}
            for r in ca
            if r.account_id in (12, 79)
        ]
        details["all_new_campaign_links_are_new_accounts"] = all(a >= 2095 for a in acct_set)

        r0_camps = []
        for cid in (1, 2, 3, 4):
            row = db.execute(
                text(
                    "SELECT id, name, status, platform::text, created_at FROM campaigns WHERE id = :id"
                ),
                {"id": cid},
            ).first()
            if row:
                r0_camps.append(
                    {
                        k: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for k, v in dict(row._mapping).items()
                    }
                )
        details["r0_campaigns_1_to_4"] = r0_camps

        # contact 92: is it linked only to test campaigns?
        c92_links = db.execute(
            text(
                """
                SELECT cr.id, cr.campaign_id, c.name, c.status, cr.created_at
                FROM campaign_recipients cr
                JOIN campaigns c ON c.id = cr.campaign_id
                WHERE cr.contact_id = 92
                ORDER BY cr.id
                """
            )
        ).fetchall()
        details["contact_92_campaign_links"] = [
            {
                k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in dict(r._mapping).items()
            }
            for r in c92_links
        ]

        # new channel_sessions account distribution
        new_sess_ids = d["diff_by_table"]["channel_sessions"]["ids_in_live_not_r0"]
        if new_sess_ids:
            sess_accts = db.execute(
                text(
                    "SELECT account_id, count(*) FROM channel_sessions WHERE id = ANY(:ids) GROUP BY account_id ORDER BY account_id"
                ),
                {"ids": new_sess_ids},
            ).fetchall()
            details["new_sessions_by_account"] = [
                {"account_id": r[0], "count": r[1]} for r in sess_accts
            ]
            details["new_sessions_on_12_or_79"] = [
                r for r in details["new_sessions_by_account"] if r["account_id"] in (12, 79)
            ]

        # Aug 29 subset of new accounts
        aug29 = [a for a in d["new_accounts_detail"] if (a.get("created_at") or "").startswith("2026-08-29")]
        aug26 = [a for a in d["new_accounts_detail"] if (a.get("created_at") or "").startswith("2026-08-26")]
        details["new_accounts_aug26_count"] = len(aug26)
        details["new_accounts_aug29_count"] = len(aug29)
        details["new_accounts_aug29_ids"] = [a["id"] for a in aug29]
        details["new_accounts_aug26_ids"] = [a["id"] for a in aug26]

        OUT.write_text(json.dumps(details, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"WROTE {OUT}")
        # compact summary
        print(
            json.dumps(
                {
                    "live_sessions": details["live_sessions_12_79"],
                    "r0_sessions": details["r0_sessions_12_79"],
                    "contacts": details["authorized_contacts"],
                    "camp_prefix": details["campaigns_by_prefix"],
                    "attempts": len(details["attempts_with_platform_message_id"]),
                    "new_pool_12_79": details["new_pool_for_12_79"],
                    "new_camps_using_12_79": details["new_campaigns_using_12_or_79"],
                    "new_sess_12_79": details.get("new_sessions_on_12_or_79"),
                    "aug26": details["new_accounts_aug26_count"],
                    "aug29": details["new_accounts_aug29_count"],
                    "schedule_195": details["schedule_195_live"],
                    "contact_92_links": details["contact_92_campaign_links"],
                },
                indent=2,
                default=str,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
