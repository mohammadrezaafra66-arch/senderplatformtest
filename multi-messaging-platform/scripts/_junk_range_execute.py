"""Execute exact-ID junk cleanup in one transaction. No BETWEEN deletes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal

PLAN = Path("/tmp/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_PLAN.json")
RESULT_JSON = Path("/tmp/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_RESULT.json")
RESULT_MD = Path("/tmp/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_RESULT.md")


def main() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    gates = plan["safety_gates"]
    if not gates.get("AUTO_DELETE_AUTHORIZED"):
        raise SystemExit("REFUSE: AUTO_DELETE_AUTHORIZED is false")
    if 2269 in plan["DELETE_ACCOUNT_IDS"]:
        raise SystemExit("REFUSE: account 2269 in delete list")
    if gates.get("ACCOUNT2269_SELECTED_FOR_DELETE"):
        raise SystemExit("REFUSE: ACCOUNT2269_SELECTED_FOR_DELETE")
    if gates.get("UNKNOWN_RECORDS_SELECTED_FOR_DELETE", 1) != 0:
        raise SystemExit("REFUSE: unknown selected")
    if gates.get("REAL_OPERATIONAL_RECORDS_SELECTED_FOR_DELETE", 1) != 0:
        raise SystemExit("REFUSE: real operational selected")
    if gates.get("LIVE_QUEUE_REFERENCES_SELECTED_FOR_DELETE", 1) != 0:
        raise SystemExit("REFUSE: live queue selected")
    if gates.get("SUCCESSFUL_REAL_SEND_RECORDS_SELECTED_FOR_DELETE", 1) != 0:
        raise SystemExit("REFUSE: success sends selected")

    camp_ids = list(plan["DELETE_CAMPAIGN_IDS"])
    acct_ids = list(plan["DELETE_ACCOUNT_IDS"])
    assert camp_ids and all(isinstance(i, int) for i in camp_ids)
    assert acct_ids and all(isinstance(i, int) for i in acct_ids)
    assert 2269 not in acct_ids
    assert 233 not in camp_ids

    db = SessionLocal()
    deleted = {
        "message_attempts": 0,
        "staged_queue_items": 0,
        "rendered_messages": 0,
        "messages": 0,
        "campaign_recipients": 0,
        "campaign_accounts": 0,
        "campaigns": 0,
        "channel_sessions": 0,
        "rubika_account_pool": 0,
        "rubika_login_challenges": 0,
        "rate_policies": 0,
        "account_send_settings": 0,
        "accounts": 0,
        "sent_registry_nulled": 0,
    }
    try:
        # Pre-check 2269 exists
        exists_2269 = db.execute(
            text("SELECT count(*) FROM accounts WHERE id=2269")
        ).scalar()
        if int(exists_2269 or 0) != 1:
            raise SystemExit("REFUSE: account 2269 missing before delete")

        # 1) message_attempts for messages in delete campaigns
        r = db.execute(
            text(
                """
                DELETE FROM message_attempts
                WHERE message_id IN (
                  SELECT id FROM messages WHERE campaign_id = ANY(:cids)
                )
                """
            ),
            {"cids": camp_ids},
        )
        deleted["message_attempts"] = r.rowcount or 0

        # 2) staged queue
        r = db.execute(
            text("DELETE FROM staged_queue_items WHERE campaign_id = ANY(:cids)"),
            {"cids": camp_ids},
        )
        deleted["staged_queue_items"] = r.rowcount or 0

        # 3) rendered messages
        r = db.execute(
            text("DELETE FROM rendered_messages WHERE campaign_id = ANY(:cids)"),
            {"cids": camp_ids},
        )
        deleted["rendered_messages"] = r.rowcount or 0

        # 4) campaign_recipients BEFORE messages
        # (campaign_recipients.final_message_id FK → messages.id)
        r = db.execute(
            text("DELETE FROM campaign_recipients WHERE campaign_id = ANY(:cids)"),
            {"cids": camp_ids},
        )
        deleted["campaign_recipients"] = r.rowcount or 0

        # 5) messages (campaign-scoped + any remaining for delete accounts except 2269)
        r = db.execute(
            text("DELETE FROM messages WHERE campaign_id = ANY(:cids)"),
            {"cids": camp_ids},
        )
        deleted["messages"] = r.rowcount or 0
        r = db.execute(
            text(
                """
                DELETE FROM messages
                WHERE account_id = ANY(:aids)
                  AND campaign_id IS DISTINCT FROM 233
                """
            ),
            {"aids": acct_ids},
        )
        deleted["messages"] += r.rowcount or 0

        # 6) campaign_accounts for delete campaigns
        r = db.execute(
            text("DELETE FROM campaign_accounts WHERE campaign_id = ANY(:cids)"),
            {"cids": camp_ids},
        )
        deleted["campaign_accounts"] = r.rowcount or 0

        # Null sent registry campaign refs (ON DELETE SET NULL exists, but explicit)
        r = db.execute(
            text(
                """
                UPDATE rubika_global_sent_registry
                SET last_sent_campaign_id = NULL
                WHERE last_sent_campaign_id = ANY(:cids)
                """
            ),
            {"cids": camp_ids},
        )
        deleted["sent_registry_nulled"] = r.rowcount or 0

        # 7) campaigns exact ids
        r = db.execute(
            text("DELETE FROM campaigns WHERE id = ANY(:cids)"),
            {"cids": camp_ids},
        )
        deleted["campaigns"] = r.rowcount or 0
        if deleted["campaigns"] != len(camp_ids):
            raise RuntimeError(
                f"campaign delete count mismatch: {deleted['campaigns']} != {len(camp_ids)}"
            )

        # 8) account children
        r = db.execute(
            text("DELETE FROM channel_sessions WHERE account_id = ANY(:aids)"),
            {"aids": acct_ids},
        )
        deleted["channel_sessions"] = r.rowcount or 0
        r = db.execute(
            text("DELETE FROM rubika_account_pool WHERE account_id = ANY(:aids)"),
            {"aids": acct_ids},
        )
        deleted["rubika_account_pool"] = r.rowcount or 0
        r = db.execute(
            text(
                "DELETE FROM rubika_login_challenges WHERE account_id = ANY(:aids)"
            ),
            {"aids": acct_ids},
        )
        deleted["rubika_login_challenges"] = r.rowcount or 0
        r = db.execute(
            text("DELETE FROM rate_policies WHERE account_id = ANY(:aids)"),
            {"aids": acct_ids},
        )
        deleted["rate_policies"] = r.rowcount or 0
        r = db.execute(
            text("DELETE FROM account_send_settings WHERE account_id = ANY(:aids)"),
            {"aids": acct_ids},
        )
        deleted["account_send_settings"] = r.rowcount or 0

        # Remaining campaign_accounts for delete accounts (should be none or only keep camps)
        r = db.execute(
            text("DELETE FROM campaign_accounts WHERE account_id = ANY(:aids)"),
            {"aids": acct_ids},
        )
        deleted["campaign_accounts"] += r.rowcount or 0

        # 9) accounts exact ids
        r = db.execute(
            text("DELETE FROM accounts WHERE id = ANY(:aids)"),
            {"aids": acct_ids},
        )
        deleted["accounts"] = r.rowcount or 0
        if deleted["accounts"] != len(acct_ids):
            raise RuntimeError(
                f"account delete count mismatch: {deleted['accounts']} != {len(acct_ids)}"
            )

        # Verify 2269 preserved
        still_2269 = db.execute(
            text("SELECT count(*) FROM accounts WHERE id=2269")
        ).scalar()
        if int(still_2269 or 0) != 1:
            raise RuntimeError("Account 2269 was not preserved")

        # Verify delete ids gone
        remain_a = db.execute(
            text("SELECT id FROM accounts WHERE id = ANY(:aids) ORDER BY id"),
            {"aids": acct_ids},
        ).scalars().all()
        remain_c = db.execute(
            text("SELECT id FROM campaigns WHERE id = ANY(:cids) ORDER BY id"),
            {"cids": camp_ids},
        ).scalars().all()
        if remain_a or remain_c:
            raise RuntimeError(f"survivors accounts={remain_a} campaigns={remain_c}")

        # Orphan checks: campaign_accounts pointing to missing
        orphan_ca = db.execute(
            text(
                """
                SELECT count(*) FROM campaign_accounts ca
                WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id=ca.account_id)
                   OR NOT EXISTS (SELECT 1 FROM campaigns c WHERE c.id=ca.campaign_id)
                """
            )
        ).scalar()
        orphan_msg = db.execute(
            text(
                """
                SELECT count(*) FROM messages m
                WHERE (m.account_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM accounts a WHERE a.id=m.account_id))
                   OR NOT EXISTS (SELECT 1 FROM campaigns c WHERE c.id=m.campaign_id)
                """
            )
        ).scalar()
        orphan_sess = db.execute(
            text(
                """
                SELECT count(*) FROM channel_sessions cs
                WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id=cs.account_id)
                """
            )
        ).scalar()
        orphan_sq = db.execute(
            text(
                """
                SELECT count(*) FROM staged_queue_items sq
                WHERE NOT EXISTS (SELECT 1 FROM campaigns c WHERE c.id=sq.campaign_id)
                """
            )
        ).scalar()

        attempts_233 = db.execute(
            text(
                """
                SELECT count(*) FROM message_attempts ma
                JOIN messages m ON m.id=ma.message_id
                WHERE m.campaign_id=233
                """
            )
        ).scalar()

        db.commit()

        result = {
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "DELETED_ACCOUNT_IDS": acct_ids,
            "DELETED_CAMPAIGN_IDS": camp_ids,
            "PRESERVED_ACCOUNT_2269": True,
            "rowcounts": deleted,
            "ORPHANED_REFERENCES_CREATED": int(orphan_ca or 0)
            + int(orphan_msg or 0)
            + int(orphan_sess or 0)
            + int(orphan_sq or 0),
            "orphan_detail": {
                "campaign_accounts": int(orphan_ca or 0),
                "messages": int(orphan_msg or 0),
                "channel_sessions": int(orphan_sess or 0),
                "staged_queue_items": int(orphan_sq or 0),
            },
            "CAMPAIGN233_ATTEMPTS": int(attempts_233 or 0),
            "KEEP_ACCOUNT_IDS": plan["KEEP_ACCOUNT_IDS"],
            "KEEP_CAMPAIGN_IDS": plan["KEEP_CAMPAIGN_IDS"],
            "ACCOUNTS_SCANNED": plan["ACCOUNTS_SCANNED"],
            "CAMPAIGNS_SCANNED": plan["CAMPAIGNS_SCANNED"],
            "ACCOUNTS_UNKNOWN": plan["ACCOUNTS_UNKNOWN"],
            "CAMPAIGNS_UNKNOWN": plan["CAMPAIGNS_UNKNOWN"],
        }
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        md = [
            "# Junk Account/Campaign Cleanup Result",
            "",
            f"Committed: {result['committed_at']}",
            "",
            f"ACCOUNTS_DELETED={len(acct_ids)}",
            f"CAMPAIGNS_DELETED={len(camp_ids)}",
            f"PRESERVED_ACCOUNT_2269=True",
            f"ORPHANED_REFERENCES_CREATED={result['ORPHANED_REFERENCES_CREATED']}",
            f"CAMPAIGN233_ATTEMPTS={result['CAMPAIGN233_ATTEMPTS']}",
            "",
            "## Deleted accounts",
            str(acct_ids),
            "",
            "## Deleted campaigns",
            str(camp_ids),
            "",
            "## Rowcounts",
            json.dumps(deleted, indent=2),
        ]
        RESULT_MD.write_text("\n".join(md), encoding="utf-8")
        print(json.dumps(result, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
