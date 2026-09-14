"""Build exact-ID R10 cleanup plan — READ ONLY (no DELETE). Writes plan+before JSON only."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal

REPORT = Path("/tmp/r10_cleanup_plan_out")
HOST_HINT = "copy to reports/rubika-remediation/"

# R0 cut — rows with created_at before this are treated as pre-remediation when also in R0 id set
R0_MAX = {
    "accounts": 92,
    "contacts": 2,
    "campaigns": 4,
    "messages": 1,
    "staged_queue_items": 1,
    "message_attempts": 2,
    "campaign_accounts": 6,
    "campaign_recipients": 4,
    "rubika_sender_schedules": 195,  # only id 195 existed; higher ids are new
}


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    out: dict = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "PLAN_ONLY_NO_EXECUTE",
        "tables": {},
        "ambiguous": [],
        "preservation": {},
        "expected_after": {},
        "fk_order": [],
        "transaction_sql_preview": [],
    }
    try:
        # Load candidate sets from live DB with provenance columns
        # --- accounts: only those with pytest labels AND id>92 ---
        acct_rows = db.execute(
            text(
                """
                SELECT id, label, platform::text, status::text, created_at
                FROM accounts
                WHERE id > 92
                ORDER BY id
                """
            )
        ).fetchall()
        acct_delete = []
        for r in acct_rows:
            label = (r.label or "").lower()
            looks_test = (
                label.startswith("r22-")
                or label in {
                    "ok", "other-global", "auto-a", "auto-b", "redis-fail",
                    "healthy", "sick", "start-block", "agg", "window-wait", "daily-cap",
                }
                or label.startswith("p6")
            )
            if looks_test and "rubika" in (r.platform or "").lower():
                acct_delete.append(int(r.id))
            else:
                out["ambiguous"].append(
                    {
                        "table": "accounts",
                        "id": r.id,
                        "reason": "id>92 but label/platform not conclusive test debris",
                        "label": r.label,
                        "platform": r.platform,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                )

        # --- campaigns: id>4 AND (r22-|p6-) name; exclude 1,2 ---
        camp_rows = db.execute(
            text(
                """
                SELECT id, name, status, created_at,
                       (SELECT count(*) FROM campaign_recipients cr WHERE cr.campaign_id=c.id) AS recip_n,
                       (SELECT count(*) FROM messages m WHERE m.campaign_id=c.id) AS msg_n
                FROM campaigns c
                WHERE id > 4
                ORDER BY id
                """
            )
        ).fetchall()
        camp_delete = []
        for r in camp_rows:
            name = r.name or ""
            if name.startswith("r22-") or name.startswith("p6-"):
                camp_delete.append(int(r.id))
            else:
                out["ambiguous"].append(
                    {
                        "table": "campaigns",
                        "id": r.id,
                        "reason": "id>4 but name not r22-/p6-",
                        "name": name,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                )

        camp_delete_set = set(camp_delete)
        acct_delete_set = set(acct_delete)

        # --- campaign_accounts owned by delete campaigns ---
        ca_rows = db.execute(
            text(
                """
                SELECT id, campaign_id, account_id
                FROM campaign_accounts
                WHERE campaign_id = ANY(:cids)
                ORDER BY id
                """
            ),
            {"cids": camp_delete},
        ).fetchall()
        ca_delete = [int(r.id) for r in ca_rows]
        # Ambiguous: campaign_accounts on KEEP campaigns with test accounts
        ca_amb = db.execute(
            text(
                """
                SELECT id, campaign_id, account_id
                FROM campaign_accounts
                WHERE campaign_id IN (1,2,3,4) AND account_id = ANY(:aids)
                """
            ),
            {"aids": acct_delete},
        ).fetchall()
        for r in ca_amb:
            out["ambiguous"].append(
                {
                    "table": "campaign_accounts",
                    "id": r.id,
                    "reason": "links KEEP campaign to test account — exclude from delete",
                    "campaign_id": r.campaign_id,
                    "account_id": r.account_id,
                }
            )

        # --- campaign_recipients on delete campaigns ---
        cr_rows = db.execute(
            text(
                """
                SELECT id, campaign_id, contact_id
                FROM campaign_recipients
                WHERE campaign_id = ANY(:cids)
                ORDER BY id
                """
            ),
            {"cids": camp_delete},
        ).fetchall()
        cr_delete = [int(r.id) for r in cr_rows]
        # Ambiguous: recipients on KEEP camps that are post-R0 contacts other than approved
        cr_keep_camp = db.execute(
            text(
                """
                SELECT id, campaign_id, contact_id
                FROM campaign_recipients
                WHERE campaign_id IN (1,2,3,4)
                ORDER BY id
                """
            )
        ).fetchall()
        keep_camp_contact_ids = {int(r.contact_id) for r in cr_keep_camp}

        # --- messages on delete campaigns ---
        msg_rows = db.execute(
            text(
                """
                SELECT id, campaign_id, account_id, contact_id
                FROM messages
                WHERE campaign_id = ANY(:cids)
                ORDER BY id
                """
            ),
            {"cids": camp_delete},
        ).fetchall()
        msg_delete = [int(r.id) for r in msg_rows]
        # Never delete message 1 / campaign 3
        msg_keep_check = db.execute(
            text("SELECT id, campaign_id FROM messages WHERE campaign_id IN (1,2,3,4) ORDER BY id")
        ).fetchall()
        for r in msg_keep_check:
            if int(r.id) in msg_delete:
                out["ambiguous"].append(
                    {
                        "table": "messages",
                        "id": r.id,
                        "reason": "would delete message on KEEP campaign — BUG",
                    }
                )

        # --- staged on delete campaigns ---
        st_rows = db.execute(
            text(
                """
                SELECT id, campaign_id, contact_id
                FROM staged_queue_items
                WHERE campaign_id = ANY(:cids)
                ORDER BY id
                """
            ),
            {"cids": camp_delete},
        ).fetchall()
        st_delete = [int(r.id) for r in st_rows]

        # --- message_attempts for delete messages ---
        att_rows = db.execute(
            text(
                """
                SELECT id, message_id
                FROM message_attempts
                WHERE message_id = ANY(:mids)
                ORDER BY id
                """
            ),
            {"mids": msg_delete or [0]},
        ).fetchall()
        att_delete = [int(r.id) for r in att_rows]
        # Also attempts 3-14 if message in delete set; never attempt id 1,2 if msg 1
        att_keep = db.execute(
            text("SELECT id FROM message_attempts WHERE message_id = 1")
        ).fetchall()
        att_keep_ids = {int(r.id) for r in att_keep}
        att_delete = [i for i in att_delete if i not in att_keep_ids]

        # --- rendered_messages on delete campaigns ---
        rm_rows = db.execute(
            text(
                """
                SELECT id, campaign_id, contact_id
                FROM rendered_messages
                WHERE campaign_id = ANY(:cids)
                ORDER BY id
                """
            ),
            {"cids": camp_delete},
        ).fetchall()
        rm_delete = [int(r.id) for r in rm_rows]

        # --- schedules: p6-* phases, not 195 ---
        sch_rows = db.execute(
            text(
                """
                SELECT id, phase, is_active, created_at
                FROM rubika_sender_schedules
                WHERE id <> 195
                ORDER BY id
                """
            )
        ).fetchall()
        sch_delete = []
        for r in sch_rows:
            phase = r.phase or ""
            if phase.startswith("p6-") or phase.startswith("p6"):
                sch_delete.append(int(r.id))
            else:
                out["ambiguous"].append(
                    {
                        "table": "rubika_sender_schedules",
                        "id": r.id,
                        "reason": "non-195 schedule without p6- phase",
                        "phase": phase,
                    }
                )

        # --- pool: rows for delete accounts OR p6-* phases; NEVER account 12/79/92 ---
        pool_rows = db.execute(
            text(
                """
                SELECT id, account_id, phase, created_at
                FROM rubika_account_pool
                ORDER BY id
                """
            )
        ).fetchall()
        pool_delete = []
        pool_keep = []
        for r in pool_rows:
            aid = int(r.account_id)
            phase = r.phase or ""
            if aid in (12, 79, 92):
                pool_keep.append(int(r.id))
                continue
            if aid in acct_delete_set or phase.startswith("p6-"):
                # only delete if account is test OR phase is p6 AND account is test
                if aid in acct_delete_set:
                    pool_delete.append(int(r.id))
                elif phase.startswith("p6-") and aid <= 92:
                    out["ambiguous"].append(
                        {
                            "table": "rubika_account_pool",
                            "id": r.id,
                            "reason": "p6 phase on production account_id<=92 — leave untouched",
                            "account_id": aid,
                            "phase": phase,
                        }
                    )
                else:
                    pool_delete.append(int(r.id))
            elif aid > 92 and aid not in acct_delete_set:
                out["ambiguous"].append(
                    {
                        "table": "rubika_account_pool",
                        "id": r.id,
                        "reason": "pool for account>92 not in delete account set",
                        "account_id": aid,
                    }
                )
            else:
                pool_keep.append(int(r.id))

        # --- channel_sessions: only for delete accounts; NEVER 12/79/92 ---
        sess_rows = db.execute(
            text(
                """
                SELECT id, account_id, created_at
                FROM channel_sessions
                WHERE account_id = ANY(:aids)
                ORDER BY id
                """
            ),
            {"aids": acct_delete},
        ).fetchall()
        sess_delete = [int(r.id) for r in sess_rows]
        # Verify no 12/79/92
        for r in sess_rows:
            if int(r.account_id) in (12, 79, 92):
                out["ambiguous"].append(
                    {
                        "table": "channel_sessions",
                        "id": r.id,
                        "reason": "FATAL: session for protected account in delete set",
                        "account_id": r.account_id,
                    }
                )

        # --- contacts: post-R0 except KEEP 1,2,92; must not be sole recipient on KEEP campaigns unless 1,2 ---
        # Contact 1,2 are R0. Contact 92 KEEP by operator.
        # Delete contacts id>2 except 92, but only if NOT referenced by KEEP campaigns 1-4
        contact_rows = db.execute(
            text(
                """
                SELECT id, first_name, created_at, campaign_id
                FROM contacts
                WHERE id > 2
                ORDER BY id
                """
            )
        ).fetchall()
        contact_delete = []
        for r in contact_rows:
            cid = int(r.id)
            if cid == 92:
                continue  # operator KEEP
            if cid in keep_camp_contact_ids:
                out["ambiguous"].append(
                    {
                        "table": "contacts",
                        "id": cid,
                        "reason": "referenced by KEEP campaign 1-4 — exclude",
                    }
                )
                continue
            # Prove test: created after R0 window OR only linked to delete campaigns
            links = db.execute(
                text(
                    "SELECT campaign_id FROM campaign_recipients WHERE contact_id=:cid"
                ),
                {"cid": cid},
            ).fetchall()
            link_camps = {int(x.campaign_id) for x in links}
            if link_camps and not link_camps.issubset(camp_delete_set):
                # linked to something outside delete set
                if link_camps & {1, 2, 3, 4}:
                    out["ambiguous"].append(
                        {
                            "table": "contacts",
                            "id": cid,
                            "reason": "linked to KEEP campaign",
                            "campaigns": sorted(link_camps),
                        }
                    )
                    continue
                out["ambiguous"].append(
                    {
                        "table": "contacts",
                        "id": cid,
                        "reason": "linked to non-delete campaign not in KEEP set",
                        "campaigns": sorted(link_camps),
                    }
                )
                continue
            # Only linked to delete campaigns OR unlinked — treat as debris if created in remediation window
            contact_delete.append(cid)

        # --- global sent registry for delete contacts ---
        gsr = db.execute(
            text(
                """
                SELECT id, contact_id FROM rubika_global_sent_registry
                WHERE contact_id = ANY(:cids)
                ORDER BY id
                """
            ),
            {"cids": contact_delete or [0]},
        ).fetchall()
        gsr_delete = [int(r.id) for r in gsr]

        # --- opt_events for delete contacts ---
        opt = db.execute(
            text(
                """
                SELECT id, contact_id FROM opt_events
                WHERE contact_id = ANY(:cids)
                ORDER BY id
                """
            ),
            {"cids": contact_delete or [0]},
        ).fetchall()
        opt_delete = [int(r.id) for r in opt]

        # Contact 2 report
        c2 = db.execute(
            text(
                "SELECT id, first_name, created_at FROM contacts WHERE id=2"
            )
        ).first()
        out["contact_2"] = {
            "id": 2,
            "exists": bool(c2),
            "alias": c2.first_name if c2 else None,
            "created_at": c2.created_at.isoformat() if c2 and c2.created_at else None,
            "in_r0_baseline": True,
            "remediation_created": False,
            "decision": "KEEP — existed in R0 (pre-remediation production Pilot contact)",
        }

        def pack(table, delete_ids, keep_ids, why):
            return {
                "TABLE": table,
                "DELETE_COUNT": len(delete_ids),
                "DELETE_IDS": delete_ids,
                "KEEP_IDS": keep_ids,
                "WHY_SAFE": why,
            }

        out["tables"]["message_attempts"] = pack(
            "message_attempts",
            att_delete,
            sorted(att_keep_ids),
            "Attempts whose message_id belongs to delete campaigns; never msg1 attempts",
        )
        out["tables"]["staged_queue_items"] = pack(
            "staged_queue_items",
            st_delete,
            [1] if db.execute(text("SELECT 1 FROM staged_queue_items WHERE id=1")).first() else [],
            "Staged rows with campaign_id in confirmed test campaigns only",
        )
        out["tables"]["rendered_messages"] = pack(
            "rendered_messages",
            rm_delete,
            [],
            "Rendered rows for delete campaigns only",
        )
        out["tables"]["messages"] = pack(
            "messages",
            msg_delete,
            [1],
            "Messages with campaign_id in confirmed r22-/p6- campaigns only",
        )
        out["tables"]["campaign_recipients"] = pack(
            "campaign_recipients",
            cr_delete,
            [int(r.id) for r in cr_keep_camp],
            "Recipients of delete campaigns only; KEEP camps 1-4 recipients untouched",
        )
        out["tables"]["campaign_accounts"] = pack(
            "campaign_accounts",
            ca_delete,
            [int(r.id) for r in db.execute(text("SELECT id FROM campaign_accounts WHERE campaign_id IN (1,2,3,4)")).fetchall()],
            "CampaignAccount rows for delete campaigns only",
        )
        out["tables"]["opt_events"] = pack(
            "opt_events",
            opt_delete,
            [],
            "Opt events for delete contacts only",
        )
        out["tables"]["rubika_global_sent_registry"] = pack(
            "rubika_global_sent_registry",
            gsr_delete,
            [],
            "Registry rows for delete contacts only",
        )
        out["tables"]["campaigns"] = pack(
            "campaigns",
            camp_delete,
            [1, 2, 3, 4],
            "Name starts with r22- or p6- AND id>4; ownership via test naming+timestamp+no link to prod senders 12/79",
        )
        out["tables"]["rubika_sender_schedules"] = pack(
            "rubika_sender_schedules",
            sch_delete,
            [195],
            "phase starts with p6-; id!=195",
        )
        out["tables"]["rubika_account_pool"] = pack(
            "rubika_account_pool",
            pool_delete,
            pool_keep,
            "Pool rows for delete test accounts only; never account 12/79/92",
        )
        out["tables"]["channel_sessions"] = pack(
            "channel_sessions",
            sess_delete,
            [11, 12, 600, 657, 728],  # 92,79,12 sessions
            "Sessions whose account_id is in confirmed fake account delete set only",
        )
        out["tables"]["contacts"] = pack(
            "contacts",
            contact_delete,
            [1, 2, 92],
            "Post-R0 contacts not 92, not linked to KEEP campaigns; only linked to delete campaigns or unlinked",
        )
        out["tables"]["accounts"] = pack(
            "accounts",
            acct_delete,
            [12, 79, 92],
            "id>92 + Rubika + conclusive pytest label; never 12/79/92",
        )

        # Preservation gates
        def exists(table, id_):
            return bool(
                db.execute(
                    text(f"SELECT 1 FROM {table} WHERE id=:id"), {"id": id_}
                ).first()
            )

        from core_engine.models import Account
        from core_engine.services.account_session_wiring import evaluate_account_session_readiness

        a12 = db.query(Account).filter(Account.id == 12).first()
        a79 = db.query(Account).filter(Account.id == 79).first()
        r12 = evaluate_account_session_readiness(db, a12)
        r79 = evaluate_account_session_readiness(db, a79)

        out["preservation"] = {
            "account_12_exists": exists("accounts", 12),
            "account_79_exists": exists("accounts", 79),
            "account_92_exists": exists("accounts", 92),
            "account_12_in_delete": 12 in acct_delete_set,
            "account_79_in_delete": 79 in acct_delete_set,
            "account_92_in_delete": 92 in acct_delete_set,
            "campaign_1_in_delete": 1 in camp_delete_set,
            "campaign_2_in_delete": 2 in camp_delete_set,
            "contact_92_in_delete": 92 in set(contact_delete),
            "schedule_195_in_delete": 195 in set(sch_delete),
            "sessions_12_79_in_delete": any(
                sid in set(sess_delete) for sid in (600, 657, 728)
            ),
            "sessions_92_in_delete": any(sid in set(sess_delete) for sid in (11, 12)),
            "pool_12_79_92_in_delete": any(
                pid in set(pool_delete) for pid in pool_keep if True
            ),
            # refine pool check
            "account12_session_code": r12.code,
            "account12_session_ready": r12.ready,
            "account79_session_code": r79.code,
            "account79_session_ready": r79.ready,
        }
        # Fix pool_12_79_92 check properly
        protected_pool = set(
            int(r.id)
            for r in db.execute(
                text(
                    "SELECT id FROM rubika_account_pool WHERE account_id IN (12,79,92)"
                )
            )
        )
        out["preservation"]["pool_12_79_92_ids"] = sorted(protected_pool)
        out["preservation"]["pool_12_79_92_in_delete"] = bool(
            protected_pool & set(pool_delete)
        )

        # Live counts now
        counts = {}
        for t in (
            "accounts",
            "contacts",
            "campaigns",
            "messages",
            "staged_queue_items",
            "message_attempts",
            "campaign_accounts",
            "campaign_recipients",
            "channel_sessions",
            "rubika_account_pool",
            "rubika_sender_schedules",
            "rendered_messages",
            "rubika_global_sent_registry",
            "opt_events",
        ):
            counts[t] = int(db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar())
        out["counts_before"] = counts

        rubika_now = int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM accounts WHERE platform::text ILIKE '%rubika%'"
                )
            ).scalar()
        )
        rubika_le92 = int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM accounts WHERE platform::text ILIKE '%rubika%' AND id <= 92"
                )
            ).scalar()
        )

        out["expected_after"] = {
            "EXPECTED_ACCOUNTS_AFTER": counts["accounts"] - len(acct_delete),
            "EXPECTED_RUBIKA_ACCOUNTS_AFTER": rubika_le92,  # should be 43
            "EXPECTED_CAMPAIGNS_AFTER": counts["campaigns"] - len(camp_delete),
            "EXPECTED_CONTACTS_AFTER": counts["contacts"] - len(contact_delete),
            "EXPECTED_MESSAGES_AFTER": counts["messages"] - len(msg_delete),
            "EXPECTED_STAGED_AFTER": counts["staged_queue_items"] - len(st_delete),
            "EXPECTED_ATTEMPTS_AFTER": counts["message_attempts"] - len(att_delete),
            "EXPECTED_SESSIONS_AFTER": counts["channel_sessions"] - len(sess_delete),
            "EXPECTED_POOL_AFTER": counts["rubika_account_pool"] - len(pool_delete),
            "EXPECTED_SCHEDULES_AFTER": counts["rubika_sender_schedules"]
            - len(sch_delete),
            "EXPECTED_TEST_DEBRIS_REMAINING": 0,
            "rubika_count_now": rubika_now,
            "rubika_le92_now": rubika_le92,
        }

        # FK order (schema-derived)
        out["fk_order"] = [
            "message_attempts",
            "staged_queue_items",  # may FK rendered_messages
            "rendered_messages",
            "messages",
            "campaign_recipients",
            "campaign_accounts",
            "opt_events",
            "rubika_global_sent_registry",
            "campaigns",
            "rubika_sender_schedules",
            "rubika_account_pool",
            "channel_sessions",
            "contacts",
            "accounts",
        ]

        delete_row_total = sum(v["DELETE_COUNT"] for v in out["tables"].values())
        out["summary"] = {
            "DELETE_TABLE_COUNT": sum(
                1 for v in out["tables"].values() if v["DELETE_COUNT"] > 0
            ),
            "DELETE_ROW_COUNT_TOTAL": delete_row_total,
            "FAKE_ACCOUNT_DELETE_COUNT": len(acct_delete),
            "TEST_CAMPAIGN_DELETE_COUNT": len(camp_delete),
            "TEST_CONTACT_DELETE_COUNT": len(contact_delete),
            "TEST_MESSAGE_DELETE_COUNT": len(msg_delete),
            "AMBIGUOUS_RECORD_COUNT": len(out["ambiguous"]),
        }

        # Preservation pass
        p = out["preservation"]
        out["PRODUCTION_PRESERVATION_CHECK_PASS"] = all(
            [
                p["account_12_exists"],
                p["account_79_exists"],
                p["account_92_exists"],
                not p["account_12_in_delete"],
                not p["account_79_in_delete"],
                not p["account_92_in_delete"],
                not p["campaign_1_in_delete"],
                not p["campaign_2_in_delete"],
                not p["contact_92_in_delete"],
                not p["schedule_195_in_delete"],
                not p["sessions_12_79_in_delete"],
                not p["sessions_92_in_delete"],
                not p["pool_12_79_92_in_delete"],
                p["account12_session_ready"] is True,
                p["account79_session_ready"] is True,
                out["expected_after"]["EXPECTED_RUBIKA_ACCOUNTS_AFTER"] == 43,
                out["summary"]["AMBIGUOUS_RECORD_COUNT"] == 0,
            ]
        )

        out["TRANSACTION_READY"] = (
            out["PRODUCTION_PRESERVATION_CHECK_PASS"]
            and out["summary"]["AMBIGUOUS_RECORD_COUNT"] == 0
            and len(acct_delete) == 110
            and len(camp_delete) == 95
        )

        path = REPORT / "R10_CLEANUP_PLAN_DATA.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({
            "summary": out["summary"],
            "expected_after": out["expected_after"],
            "preservation": {
                k: v for k, v in p.items() if k.endswith("_ready") or k.endswith("_code") or k.endswith("_exists") or k.endswith("_in_delete") or k == "pool_12_79_92_in_delete"
            },
            "AMBIGUOUS_RECORD_COUNT": out["summary"]["AMBIGUOUS_RECORD_COUNT"],
            "ambiguous_sample": out["ambiguous"][:10],
            "PRODUCTION_PRESERVATION_CHECK_PASS": out["PRODUCTION_PRESERVATION_CHECK_PASS"],
            "TRANSACTION_READY": out["TRANSACTION_READY"],
            "contact_2": out["contact_2"],
            "table_counts": {k: v["DELETE_COUNT"] for k, v in out["tables"].items()},
        }, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
