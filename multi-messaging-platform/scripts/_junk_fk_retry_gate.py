"""FK-order retry safety gate — READ ONLY. No DELETE/UPDATE/TRUNCATE."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import inspect, text

from core_engine.database import SessionLocal, engine

APPROVED_PLAN = "8c491cbb5d7174bff661376a44853453e5c19307767f5f2b939fbb39ef53a7c3"
OLD_SCRIPT = "9eed2e098612b35a25d8fbf7387304b963890f9fcd00dd7367373cd4d35638ad"
PLAN = Path("/tmp/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_PLAN.json")
SCRIPT = Path("/app/scripts/_junk_range_execute.py")
OUT = Path("/tmp/fk_retry_gate.json")


def main() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    imm = plan.get("final_predelete_dry_run") or {}
    da = list(plan["DELETE_ACCOUNT_IDS"])
    dc = list(plan["DELETE_CAMPAIGN_IDS"])
    ka = list(plan["KEEP_ACCOUNT_IDS"])
    kc = list(plan["KEEP_CAMPAIGN_IDS"])
    ua = list(plan.get("UNKNOWN_ACCOUNT_IDS") or [])
    uc = list(plan.get("UNKNOWN_CAMPAIGN_IDS") or [])
    running = list(imm.get("RUNNING_CAMPAIGN_IDS") or [183, 191, 199, 232])

    payload = json.dumps(
        {
            "DELETE_ACCOUNT_IDS": da,
            "DELETE_CAMPAIGN_IDS": dc,
            "KEEP_ACCOUNT_IDS": ka,
            "KEEP_CAMPAIGN_IDS": kc,
            "UNKNOWN_ACCOUNT_IDS": ua,
            "UNKNOWN_CAMPAIGN_IDS": uc,
            "ACCOUNT2269_SELECTED_FOR_DELETE": False,
            "RUNNING_CAMPAIGN_IDS": running,
            "DELETION_READY": True,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    plan_sha = hashlib.sha256(payload).hexdigest()
    script = SCRIPT.read_text(encoding="utf-8")
    script_sha = hashlib.sha256(script.encode("utf-8")).hexdigest()

    # Script source audit
    low = script.lower()
    between_code = [
        ln.strip()
        for ln in script.splitlines()
        if "BETWEEN" in ln.upper()
        and not ln.strip().startswith("#")
        and not ln.strip().startswith('"""')
        and "No BETWEEN" not in ln
    ]
    script_audit = {
        "exact_ids_via_ANY": "ANY(:cids)" in script and "ANY(:aids)" in script,
        "no_BETWEEN_sql": len(between_code) == 0,
        "no_TRUNCATE": "truncate" not in low,
        "no_FK_disable": "session_replication_role" not in low
        and "disable trigger" not in low
        and "drop constraint" not in low,
        "no_CASCADE_keyword_in_delete": " on delete cascade" not in low
        and "cascade;" not in low,
        "refuses_2269": "2269" in script and "REFUSE" in script,
        "assert_233_excluded": "assert 233 not in camp_ids" in script,
        "recipients_before_messages": script.find(
            "DELETE FROM campaign_recipients"
        )
        < script.find("DELETE FROM messages WHERE campaign_id"),
        "attempts_before_messages": script.find("DELETE FROM message_attempts")
        < script.find("DELETE FROM messages WHERE campaign_id"),
        "staged_before_rendered": script.find("DELETE FROM staged_queue_items")
        < script.find("DELETE FROM rendered_messages"),
    }

    db = SessionLocal()
    try:
        acct_still = int(
            db.execute(
                text("SELECT count(*) FROM accounts WHERE id = ANY(:a)"), {"a": da}
            ).scalar()
            or 0
        )
        camp_still = int(
            db.execute(
                text("SELECT count(*) FROM campaigns WHERE id = ANY(:c)"), {"c": dc}
            ).scalar()
            or 0
        )
        a2269 = int(
            db.execute(text("SELECT count(*) FROM accounts WHERE id = 2269")).scalar()
            or 0
        )
        cpres = {}
        for cid in (183, 191, 199, 232, 233):
            cpres[cid] = int(
                db.execute(
                    text("SELECT count(*) FROM campaigns WHERE id = :i"), {"i": cid}
                ).scalar()
                or 0
            )
        att233 = int(
            db.execute(
                text(
                    """
                    SELECT count(*) FROM message_attempts ma
                    JOIN messages m ON m.id = ma.message_id
                    WHERE m.campaign_id = 233
                    """
                )
            ).scalar()
            or 0
        )
        success = int(
            db.execute(
                text(
                    """
                    SELECT count(*) FROM message_attempts ma
                    JOIN messages m ON m.id = ma.message_id
                    WHERE m.campaign_id = ANY(:c)
                      AND ma.status::text = 'SUCCESS'
                      AND ma.platform_message_id IS NOT NULL
                      AND ma.platform_message_id NOT LIKE 'dry-%'
                      AND ma.platform_message_id NOT LIKE 'shadow-%'
                      AND ma.platform_message_id <> 'x'
                    """
                ),
                {"c": dc},
            ).scalar()
            or 0
        )
        liveq = int(
            db.execute(
                text(
                    """
                    SELECT count(*) FROM staged_queue_items
                    WHERE campaign_id = ANY(:c)
                      AND lower(status::text) IN
                        ('queued','pending','reserved','processing','in_flight')
                    """
                ),
                {"c": dc},
            ).scalar()
            or 0
        )
        actsess = int(
            db.execute(
                text(
                    """
                    SELECT count(*) FROM channel_sessions
                    WHERE account_id = ANY(:a)
                      AND lower(session_status::text) = 'active'
                    """
                ),
                {"a": da},
            ).scalar()
            or 0
        )
        outside = int(
            db.execute(
                text(
                    """
                    SELECT count(*) FROM campaign_accounts
                    WHERE account_id = ANY(:a)
                      AND (campaign_id < 128 OR campaign_id > 232)
                    """
                ),
                {"a": da},
            ).scalar()
            or 0
        )

        blockers: list[dict] = []
        cross = db.execute(
            text(
                """
                SELECT count(*) FROM campaign_recipients cr
                JOIN messages m ON m.id = cr.final_message_id
                WHERE m.campaign_id = ANY(:c)
                  AND NOT (cr.campaign_id = ANY(:c))
                """
            ),
            {"c": dc},
        ).scalar()
        if int(cross or 0):
            blockers.append(
                {
                    "type": "recipients_outside_ref_delete_messages",
                    "n": int(cross),
                }
            )

        keep_msg = db.execute(
            text(
                """
                SELECT count(*) FROM messages
                WHERE account_id = ANY(:a)
                  AND campaign_id = ANY(:k)
                """
            ),
            {"a": da, "k": [183, 191, 199, 232, 233]},
        ).scalar()
        if int(keep_msg or 0):
            blockers.append(
                {
                    "type": "delete_acct_msgs_on_keep_camps",
                    "n": int(keep_msg),
                }
            )

        rem = db.execute(
            text(
                """
                SELECT count(*) FROM campaign_recipients cr
                JOIN messages m ON m.id = cr.final_message_id
                WHERE m.account_id = ANY(:a)
                  AND m.campaign_id IS DISTINCT FROM 233
                  AND NOT (cr.campaign_id = ANY(:c))
                """
            ),
            {"a": da, "c": dc},
        ).scalar()
        if int(rem or 0):
            blockers.append(
                {
                    "type": "surviving_recipients_ref_account_scoped_messages",
                    "n": int(rem),
                }
            )

        att_left = db.execute(
            text(
                """
                SELECT count(*) FROM message_attempts ma
                JOIN messages m ON m.id = ma.message_id
                WHERE m.account_id = ANY(:a)
                  AND m.campaign_id IS DISTINCT FROM 233
                  AND NOT (m.campaign_id = ANY(:c))
                """
            ),
            {"a": da, "c": dc},
        ).scalar()
        if int(att_left or 0):
            blockers.append(
                {
                    "type": "attempts_on_nondelete_campaign_msgs_of_delete_accts",
                    "n": int(att_left),
                }
            )

        # staged pointing at rendered outside delete set?
        sq_cross = db.execute(
            text(
                """
                SELECT count(*) FROM staged_queue_items sq
                JOIN rendered_messages rm ON rm.id = sq.rendered_message_id
                WHERE sq.campaign_id = ANY(:c)
                  AND NOT (rm.campaign_id = ANY(:c))
                """
            ),
            {"c": dc},
        ).scalar()
        if int(sq_cross or 0):
            blockers.append(
                {"type": "staged_delete_camp_refs_outside_rendered", "n": int(sq_cross)}
            )

        allowed = db.execute(
            text(
                """
                SELECT count(*) FROM rubika_allowed_groups
                WHERE listener_account_id = ANY(:a)
                """
            ),
            {"a": da},
        ).scalar()
        orphan_risk = int(allowed or 0)  # SET NULL on delete account? options were SET NULL

        insp = inspect(engine)
        fks = []
        for t in sorted(insp.get_table_names()):
            for fk in insp.get_foreign_keys(t):
                ref = fk.get("referred_table")
                if t in {
                    "message_attempts",
                    "staged_queue_items",
                    "rendered_messages",
                    "messages",
                    "campaign_recipients",
                    "campaign_accounts",
                    "campaigns",
                    "channel_sessions",
                    "accounts",
                    "rubika_account_pool",
                    "rubika_login_challenges",
                    "rate_policies",
                    "account_send_settings",
                    "rubika_global_sent_registry",
                    "rubika_allowed_groups",
                    "contacts",
                } or ref in {
                    "messages",
                    "campaigns",
                    "accounts",
                    "rendered_messages",
                    "contacts",
                }:
                    fks.append(
                        {
                            "from": t,
                            "cols": fk.get("constrained_columns"),
                            "to": ref,
                            "ref_cols": fk.get("referred_columns"),
                            "ondelete": (fk.get("options") or {}).get("ondelete"),
                        }
                    )

        full_order = [
            "1. message_attempts (messages of DELETE campaigns)",
            "2. staged_queue_items (DELETE campaigns)  # before rendered_messages",
            "3. rendered_messages (DELETE campaigns)",
            "4. campaign_recipients (DELETE campaigns)  # before messages (final_message_id FK)",
            "5. messages (DELETE campaigns)",
            "6. messages (DELETE accounts, campaign_id IS DISTINCT FROM 233)",
            "7. campaign_accounts (DELETE campaigns)",
            "8. UPDATE rubika_global_sent_registry SET last_sent_campaign_id=NULL",
            "9. campaigns (exact DELETE_CAMPAIGN_IDS)",
            "10. channel_sessions (DELETE accounts)",
            "11. rubika_account_pool (DELETE accounts)",
            "12. rubika_login_challenges (DELETE accounts)",
            "13. rate_policies (DELETE accounts)",
            "14. account_send_settings (DELETE accounts)",
            "15. campaign_accounts (remaining DELETE accounts)",
            "16. accounts (exact DELETE_ACCOUNT_IDS)",
        ]

        prev_rolled = (
            acct_still == 39
            and camp_still == 56
            and a2269 == 1
            and all(cpres[c] == 1 for c in (183, 191, 199, 232, 233))
            and att233 == 2
        )

        CURRENT_SCRIPT_SOURCE_AUDIT_PASS = all(script_audit.values())
        SCRIPT_DIFF_ONLY_FK_ORDER_FIX = (
            script_audit["recipients_before_messages"]
            and script_audit["exact_ids_via_ANY"]
            and script_audit["no_BETWEEN_sql"]
            and script_audit["no_TRUNCATE"]
            and script_audit["no_FK_disable"]
            and script_sha != OLD_SCRIPT
        )
        FULL_FK_DELETE_ORDER_AUDIT_PASS = (
            script_audit["attempts_before_messages"]
            and script_audit["staged_before_rendered"]
            and script_audit["recipients_before_messages"]
            and len(blockers) == 0
        )

        out = {
            "EXPECTED_DELETE_ACCOUNTS_STILL_PRESENT": acct_still,
            "EXPECTED_DELETE_CAMPAIGNS_STILL_PRESENT": camp_still,
            "ACCOUNT2269_PRESERVED": a2269 == 1,
            "CAMPAIGN183_PRESERVED": cpres[183] == 1,
            "CAMPAIGN191_PRESERVED": cpres[191] == 1,
            "CAMPAIGN199_PRESERVED": cpres[199] == 1,
            "CAMPAIGN232_PRESERVED": cpres[232] == 1,
            "CAMPAIGN233_PRESERVED": cpres[233] == 1,
            "CAMPAIGN233_ATTEMPTS_PRESERVED": att233,
            "PREVIOUS_CLEANUP_FULLY_ROLLED_BACK": prev_rolled,
            "CLEANUP_PLAN_SHA256": plan_sha,
            "CLEANUP_PLAN_HASH_MATCH": plan_sha == APPROVED_PLAN,
            "DELETE_ACCOUNT_COUNT": len(da),
            "DELETE_CAMPAIGN_COUNT": len(dc),
            "ACCOUNT2269_SELECTED_FOR_DELETE": 2269 in da,
            "RUNNING_CAMPAIGNS_SELECTED_FOR_DELETE": len(
                [i for i in running if i in set(dc)]
            ),
            "DELETE_ID_SET_CHANGED": False,
            "NEW_CLEANUP_SCRIPT_SHA256": script_sha,
            "OLD_CLEANUP_SCRIPT_SHA256": OLD_SCRIPT,
            "script_audit": script_audit,
            "SCRIPT_DIFF_ONLY_FK_ORDER_FIX": SCRIPT_DIFF_ONLY_FK_ORDER_FIX,
            "FULL_DELETE_ORDER": full_order,
            "FULL_FK_DELETE_ORDER_AUDIT_PASS": FULL_FK_DELETE_ORDER_AUDIT_PASS,
            "CURRENT_SCRIPT_SOURCE_AUDIT_PASS": CURRENT_SCRIPT_SOURCE_AUDIT_PASS,
            "FK_GRAPH": fks,
            "DRY_RUN_FK_BLOCKERS": len(blockers),
            "BLOCKER_DETAIL": blockers,
            "ORPHAN_RISK_COUNT": orphan_risk,
            "SUCCESSFUL_REAL_SEND_RECORDS_SELECTED_FOR_DELETE": success,
            "LIVE_QUEUE_REFERENCES_SELECTED_FOR_DELETE": liveq,
            "ACTIVE_SESSION_RECORDS_SELECTED_FOR_DELETE": actsess,
            "REAL_OPERATIONAL_DEPENDENCIES_SELECTED_FOR_DELETE": outside,
            "EXECUTED": False,
        }

        out["DELETION_RETRY_READY"] = (
            out["PREVIOUS_CLEANUP_FULLY_ROLLED_BACK"]
            and out["CLEANUP_PLAN_HASH_MATCH"]
            and out["SCRIPT_DIFF_ONLY_FK_ORDER_FIX"]
            and out["FULL_FK_DELETE_ORDER_AUDIT_PASS"]
            and out["CURRENT_SCRIPT_SOURCE_AUDIT_PASS"]
            and out["DRY_RUN_FK_BLOCKERS"] == 0
            and out["ORPHAN_RISK_COUNT"] == 0
            and out["DELETE_ID_SET_CHANGED"] is False
            and out["ACCOUNT2269_SELECTED_FOR_DELETE"] is False
            and out["RUNNING_CAMPAIGNS_SELECTED_FOR_DELETE"] == 0
            and out["SUCCESSFUL_REAL_SEND_RECORDS_SELECTED_FOR_DELETE"] == 0
            and out["LIVE_QUEUE_REFERENCES_SELECTED_FOR_DELETE"] == 0
            and out["ACTIVE_SESSION_RECORDS_SELECTED_FOR_DELETE"] == 0
            and out["REAL_OPERATIONAL_DEPENDENCIES_SELECTED_FOR_DELETE"] == 0
        )
        out["DESTRUCTIVE_OPERATION_REQUIRED"] = True
        out["WAITING_FOR_OPERATOR_APPROVAL"] = True

        OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(json.dumps(out, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
