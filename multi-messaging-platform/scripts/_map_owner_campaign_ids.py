"""READ-ONLY: map exact campaign names to IDs. No secrets/phones/message text."""
from __future__ import annotations

from sqlalchemy import text
from core_engine.database import SessionLocal

NAMES = ("rubika test1", "rubikaa test 2")


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    c.id AS campaign_id,
                    c.name,
                    c.status,
                    c.created_at,
                    c.updated_at,
                    (SELECT COUNT(*) FROM campaign_recipients cr WHERE cr.campaign_id = c.id)
                        AS recipient_count,
                    (SELECT COALESCE(
                        string_agg(ca.account_id::text, ',' ORDER BY ca.priority, ca.id), ''
                     )
                     FROM campaign_accounts ca
                     WHERE ca.campaign_id = c.id AND ca.enabled = true)
                        AS account_ids
                FROM campaigns c
                WHERE c.name = ANY(:names)
                ORDER BY c.id
                """
            ),
            {"names": list(NAMES)},
        ).fetchall()
        print("EXACT_MATCH_COUNT", len(rows))
        for r in rows:
            d = dict(r._mapping)
            # creator from audit — username only, no details blob
            creator = db.execute(
                text(
                    """
                    SELECT username, timestamp
                    FROM audit_logs
                    WHERE action = 'create_campaign'
                      AND resource_type = 'campaign'
                      AND resource_id = :rid
                    ORDER BY id ASC
                    LIMIT 1
                    """
                ),
                {"rid": str(d["campaign_id"])},
            ).first()
            print("---")
            print("campaign_id", d["campaign_id"])
            print("name", d["name"])
            print("status", d["status"])
            print("created_at", d["created_at"])
            print("updated_at", d["updated_at"])
            print("creator_username", creator.username if creator else None)
            print("creator_timestamp", creator.timestamp if creator else None)
            print("recipient_count", d["recipient_count"])
            print("account_ids", d["account_ids"])

        if len(rows) < 2:
            print("FALLBACK_RECENT_RUBIKA")
            recent = db.execute(
                text(
                    """
                    SELECT id, name, status, created_at
                    FROM campaigns
                    WHERE platform::text ILIKE '%rubika%'
                    ORDER BY id DESC
                    LIMIT 15
                    """
                )
            ).fetchall()
            for r in recent:
                name = r.name or ""
                # mask middle of long synthetic names; keep short human names readable for owner ID
                if len(name) > 24:
                    shown = name[:10] + "…" + name[-6:]
                else:
                    shown = name
                print(f"id={r.id} name={shown!r} status={r.status} created_at={r.created_at}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
