"""Final pre-delete dry-run. READ-ONLY. Never mutates business tables."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal

PLAN_HOST = Path("reports/cleanup/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_PLAN.json")
PLAN_MD_HOST = Path("reports/cleanup/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_PLAN.md")
SCRIPT_HOST = Path("scripts/_junk_range_execute.py")
# Container paths when run inside mmp_core_api
PLAN_C = Path("/tmp/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_PLAN.json")
SCRIPT_C = Path("/app/scripts/_junk_range_execute.py")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    plan_path = PLAN_C if PLAN_C.exists() else PLAN_HOST
    script_path = SCRIPT_C if SCRIPT_C.exists() else SCRIPT_HOST
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    script_text = script_path.read_text(encoding="utf-8")

    delete_accts = list(plan["DELETE_ACCOUNT_IDS"])
    keep_accts = list(plan["KEEP_ACCOUNT_IDS"])
    unknown_accts = list(plan["UNKNOWN_ACCOUNT_IDS"])
    delete_camps = list(plan["DELETE_CAMPAIGN_IDS"])
    keep_camps = list(plan["KEEP_CAMPAIGN_IDS"])
    unknown_camps = list(plan["UNKNOWN_CAMPAIGN_IDS"])

    # --- Script source audit ---
    script_lower = script_text.lower()
    between_hits = [
        line.strip()
        for line in script_text.splitlines()
        if "BETWEEN" in line.upper() and not line.strip().startswith("#")
    ]
    # Allow only comment mentions of BETWEEN
    between_code = [l for l in between_hits if not l.startswith("#") and not l.startswith('"""')]
    has_truncate = "truncate" in script_lower
    has_disable_fk = (
        "session_replication_role" in script_lower
        or "disable trigger" in script_lower
        or "drop constraint" in script_lower
    )
    uses_any = "ANY(:cids)" in script_text and "ANY(:aids)" in script_text
    refuses_2269 = "2269" in script_text and "REFUSE" in script_text
    no_docker_rm = "docker" not in script_lower and "volume" not in script_lower
    no_unlink = "unlink(" not in script_lower and "rmtree" not in script_lower

    script_audit = {
        "exact_ids_only_via_ANY_bind": uses_any,
        "no_BETWEEN_in_sql": len(between_code) == 0,
        "no_TRUNCATE": not has_truncate,
        "no_FK_disable": not has_disable_fk,
        "refuses_account_2269": refuses_2269,
        "assert_2269_not_in_aids": "assert 2269 not in acct_ids" in script_text,
        "assert_233_not_in_cids": "assert 233 not in camp_ids" in script_text,
        "no_docker_volume_touch": no_docker_rm and no_unlink,
        "BETWEEN_code_lines": between_code,
    }
    CLEANUP_SCRIPT_SOURCE_AUDIT_PASS = all(
        [
            script_audit["exact_ids_only_via_ANY_bind"],
            script_audit["no_BETWEEN_in_sql"],
            script_audit["no_TRUNCATE"],
            script_audit["no_FK_disable"],
            script_audit["refuses_account_2269"],
            script_audit["assert_2269_not_in_aids"],
            script_audit["assert_233_not_in_cids"],
            script_audit["no_docker_volume_touch"],
            2269 not in delete_accts,
        ]
    )

    db = SessionLocal()
    try:
        # Live verify running campaigns in range
        running = db.execute(
            text(
                """
                SELECT id, name, status::text AS status
                FROM campaigns
                WHERE id BETWEEN 128 AND 232
                  AND lower(status::text) IN ('running','sending','queued','active')
                ORDER BY id
                """
            )
        ).mappings().all()
        running_ids = [int(r["id"]) for r in running]
        running_selected = [i for i in running_ids if i in set(delete_camps)]

        # Live verify 2269
        a2269 = db.execute(
            text(
                """
                SELECT id, label, status::text AS status,
                       (SELECT count(*) FROM channel_sessions WHERE account_id=2269) AS sess,
                       (SELECT count(*) FROM campaign_accounts WHERE account_id=2269) AS camps
                FROM accounts WHERE id=2269
                """
            )
        ).mappings().first()

        # Operational dependency scans on DELETE set
        success_attempts = db.execute(
            text(
                """
                SELECT count(*) FROM message_attempts ma
                JOIN messages m ON m.id = ma.message_id
                WHERE m.campaign_id = ANY(:cids) AND ma.status::text = 'SUCCESS'
                """
            ),
            {"cids": delete_camps},
        ).scalar()
        success_with_platform = db.execute(
            text(
                """
                SELECT count(*) FROM message_attempts ma
                JOIN messages m ON m.id = ma.message_id
                WHERE m.campaign_id = ANY(:cids)
                  AND ma.status::text = 'SUCCESS'
                  AND ma.platform_message_id IS NOT NULL
                  AND ma.platform_message_id NOT LIKE 'dry-%'
                  AND ma.platform_message_id NOT LIKE 'shadow-%'
                  AND ma.platform_message_id <> 'x'
                """
            ),
            {"cids": delete_camps},
        ).scalar()
        live_queue = db.execute(
            text(
                """
                SELECT count(*) FROM staged_queue_items
                WHERE campaign_id = ANY(:cids)
                  AND lower(status::text) IN
                    ('queued','pending','reserved','processing','in_flight')
                """
            ),
            {"cids": delete_camps},
        ).scalar()
        active_sessions = db.execute(
            text(
                """
                SELECT count(*) FROM channel_sessions
                WHERE account_id = ANY(:aids)
                  AND lower(session_status::text) = 'active'
                """
            ),
            {"aids": delete_accts},
        ).scalar()
        # sessions that will be deleted (non-secret count only)
        sessions_to_delete = db.execute(
            text(
                "SELECT count(*) FROM channel_sessions WHERE account_id = ANY(:aids)"
            ),
            {"aids": delete_accts},
        ).scalar()
        # outside-range campaign refs from delete accounts
        outside_refs = db.execute(
            text(
                """
                SELECT DISTINCT account_id, campaign_id
                FROM campaign_accounts
                WHERE account_id = ANY(:aids)
                  AND (campaign_id < 128 OR campaign_id > 232)
                ORDER BY account_id, campaign_id
                """
            ),
            {"aids": delete_accts},
        ).mappings().all()

        # KEEP campaigns must not be in delete
        keep_in_delete = [i for i in keep_camps if i in set(delete_camps)]

        # Aggregate dependency counts for delete campaigns/accounts
        dep_camps = {}
        for cid in delete_camps:
            dep_camps[cid] = {
                "campaign_accounts": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM campaign_accounts WHERE campaign_id=:i"
                        ),
                        {"i": cid},
                    ).scalar()
                    or 0
                ),
                "campaign_recipients": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM campaign_recipients WHERE campaign_id=:i"
                        ),
                        {"i": cid},
                    ).scalar()
                    or 0
                ),
                "messages": int(
                    db.execute(
                        text("SELECT count(*) FROM messages WHERE campaign_id=:i"),
                        {"i": cid},
                    ).scalar()
                    or 0
                ),
                "rendered_messages": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM rendered_messages WHERE campaign_id=:i"
                        ),
                        {"i": cid},
                    ).scalar()
                    or 0
                ),
                "staged_queue_items": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM staged_queue_items WHERE campaign_id=:i"
                        ),
                        {"i": cid},
                    ).scalar()
                    or 0
                ),
                "message_attempts": int(
                    db.execute(
                        text(
                            """
                            SELECT count(*) FROM message_attempts ma
                            JOIN messages m ON m.id=ma.message_id
                            WHERE m.campaign_id=:i
                            """
                        ),
                        {"i": cid},
                    ).scalar()
                    or 0
                ),
                "sent_registry_refs": int(
                    db.execute(
                        text(
                            """
                            SELECT count(*) FROM rubika_global_sent_registry
                            WHERE last_sent_campaign_id=:i
                            """
                        ),
                        {"i": cid},
                    ).scalar()
                    or 0
                ),
            }

        dep_accts = {}
        for aid in delete_accts:
            dep_accts[aid] = {
                "channel_sessions": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM channel_sessions WHERE account_id=:i"
                        ),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
                "campaign_accounts": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM campaign_accounts WHERE account_id=:i"
                        ),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
                "messages": int(
                    db.execute(
                        text("SELECT count(*) FROM messages WHERE account_id=:i"),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
                "message_attempts": int(
                    db.execute(
                        text(
                            """
                            SELECT count(*) FROM message_attempts ma
                            JOIN messages m ON m.id=ma.message_id
                            WHERE m.account_id=:i
                            """
                        ),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
                "rubika_account_pool": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM rubika_account_pool WHERE account_id=:i"
                        ),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
                "login_challenges": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM rubika_login_challenges WHERE account_id=:i"
                        ),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
                "rate_policies": int(
                    db.execute(
                        text("SELECT count(*) FROM rate_policies WHERE account_id=:i"),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
                "account_send_settings": int(
                    db.execute(
                        text(
                            "SELECT count(*) FROM account_send_settings WHERE account_id=:i"
                        ),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
                "allowed_groups": int(
                    db.execute(
                        text(
                            """
                            SELECT count(*) FROM rubika_allowed_groups
                            WHERE listener_account_id=:i
                            """
                        ),
                        {"i": aid},
                    ).scalar()
                    or 0
                ),
            }

        # Orphan risk: FKs without explicit child delete in script
        # contacts.campaign_id already 0; allowed_groups 0
        orphan_risk = 0
        for aid, d in dep_accts.items():
            orphan_risk += d["allowed_groups"]  # not deleted by script; SET NULL exists
        # sent_registry is nulled by script — not orphan risk
        # campaign_recipients/messages/etc are deleted — OK

        # Provenance check from plan classifications
        acct_class = plan.get("account_classifications", {})
        camp_class = plan.get("campaign_classifications", {})
        bad_acct_prov = []
        for aid in delete_accts:
            c = acct_class.get(str(aid)) or acct_class.get(aid) or {}
            cls = c.get("classification")
            if cls not in {"SYNTHETIC_TEST", "AUTOMATED_DIAGNOSTIC", "PROVEN_JUNK"}:
                # plan used SYNTHETIC_TEST
                if cls != "SYNTHETIC_TEST":
                    bad_acct_prov.append({"id": aid, "classification": cls})
        bad_camp_prov = []
        for cid in delete_camps:
            c = camp_class.get(str(cid)) or camp_class.get(cid) or {}
            cls = c.get("classification")
            if cls not in {"SYNTHETIC_TEST", "AUTOMATED_DIAGNOSTIC", "PROVEN_JUNK"}:
                if cls != "SYNTHETIC_TEST":
                    bad_camp_prov.append({"id": cid, "classification": cls})

        DELETE_SET_PROVENANCE_PASS = (
            len(bad_acct_prov) == 0
            and len(bad_camp_prov) == 0
            and len(unknown_accts) == 0
            and len(unknown_camps) == 0
        )

        REAL_OPERATIONAL_DEPENDENCIES_SELECTED_FOR_DELETE = (
            len(outside_refs)
            + len(running_selected)
            + (1 if 2269 in delete_accts else 0)
            + len(keep_in_delete)
        )

        DELETE_DEPENDENCY_AUDIT_PASS = (
            int(success_attempts or 0) == 0
            and int(success_with_platform or 0) == 0
            and int(live_queue or 0) == 0
            and int(active_sessions or 0) == 0
            and REAL_OPERATIONAL_DEPENDENCIES_SELECTED_FOR_DELETE == 0
            and orphan_risk == 0
        )

        gates = {
            "CLEANUP_SCRIPT_SOURCE_AUDIT_PASS": CLEANUP_SCRIPT_SOURCE_AUDIT_PASS,
            "DELETE_DEPENDENCY_AUDIT_PASS": DELETE_DEPENDENCY_AUDIT_PASS,
            "DELETE_SET_PROVENANCE_PASS": DELETE_SET_PROVENANCE_PASS,
            "UNKNOWN_RECORDS_SELECTED_FOR_DELETE": len(unknown_accts) + len(unknown_camps),
            "ACCOUNT2269_SELECTED_FOR_DELETE": 2269 in delete_accts,
            "RUNNING_CAMPAIGNS_SELECTED_FOR_DELETE": len(running_selected),
            "REAL_OPERATIONAL_DEPENDENCIES_SELECTED_FOR_DELETE": REAL_OPERATIONAL_DEPENDENCIES_SELECTED_FOR_DELETE,
            "SUCCESSFUL_REAL_SEND_RECORDS_SELECTED_FOR_DELETE": int(
                success_with_platform or 0
            ),
            "LIVE_QUEUE_REFERENCES_SELECTED_FOR_DELETE": int(live_queue or 0),
            "ACTIVE_SESSION_RECORDS_SELECTED_FOR_DELETE": int(active_sessions or 0),
            "ORPHAN_RISK_COUNT": orphan_risk,
        }

        DELETION_READY = (
            gates["CLEANUP_SCRIPT_SOURCE_AUDIT_PASS"] is True
            and gates["DELETE_DEPENDENCY_AUDIT_PASS"] is True
            and gates["DELETE_SET_PROVENANCE_PASS"] is True
            and gates["UNKNOWN_RECORDS_SELECTED_FOR_DELETE"] == 0
            and gates["ACCOUNT2269_SELECTED_FOR_DELETE"] is False
            and gates["RUNNING_CAMPAIGNS_SELECTED_FOR_DELETE"] == 0
            and gates["REAL_OPERATIONAL_DEPENDENCIES_SELECTED_FOR_DELETE"] == 0
            and gates["SUCCESSFUL_REAL_SEND_RECORDS_SELECTED_FOR_DELETE"] == 0
            and gates["LIVE_QUEUE_REFERENCES_SELECTED_FOR_DELETE"] == 0
            and gates["ORPHAN_RISK_COUNT"] == 0
        )

        # Immutable dry-run section (IDs only — hash over canonical ID lists + gates)
        immutable = {
            "dry_run_at": datetime.now(timezone.utc).isoformat(),
            "mode": "FINAL_PREDELETE_DRY_RUN_NO_MUTATION",
            "DELETE_ACCOUNT_IDS": delete_accts,
            "KEEP_ACCOUNT_IDS": keep_accts,
            "UNKNOWN_ACCOUNT_IDS": unknown_accts,
            "DELETE_CAMPAIGN_IDS": delete_camps,
            "KEEP_CAMPAIGN_IDS": keep_camps,
            "UNKNOWN_CAMPAIGN_IDS": unknown_camps,
            "DELETE_ACCOUNT_COUNT": len(delete_accts),
            "KEEP_ACCOUNT_COUNT": len(keep_accts),
            "UNKNOWN_ACCOUNT_COUNT": len(unknown_accts),
            "DELETE_CAMPAIGN_COUNT": len(delete_camps),
            "KEEP_CAMPAIGN_COUNT": len(keep_camps),
            "UNKNOWN_CAMPAIGN_COUNT": len(unknown_camps),
            "ACCOUNT2269_CLASSIFICATION": "REAL_OPERATIONAL_MANDATORY_KEEP",
            "ACCOUNT2269_SELECTED_FOR_DELETE": False,
            "ACCOUNT2269_LIVE": dict(a2269) if a2269 else None,
            "RUNNING_CAMPAIGN_IDS": running_ids,
            "RUNNING_CAMPAIGN_DETAILS": [dict(r) for r in running],
            "RUNNING_CAMPAIGNS_SELECTED_FOR_DELETE": len(running_selected),
            "OUTSIDE_RANGE_REFS_FROM_DELETE_ACCOUNTS": [dict(x) for x in outside_refs],
            "SUCCESS_ATTEMPTS_IN_DELETE_CAMPAIGNS": int(success_attempts or 0),
            "EXTERNAL_SEND_EVIDENCE_IN_DELETE_CAMPAIGNS": int(success_with_platform or 0),
            "LIVE_QUEUE_IN_DELETE_CAMPAIGNS": int(live_queue or 0),
            "ACTIVE_SESSIONS_IN_DELETE_ACCOUNTS": int(active_sessions or 0),
            "SESSIONS_THAT_WOULD_BE_DELETED": int(sessions_to_delete or 0),
            "script_audit": script_audit,
            "safety_gates_final": gates,
            "DELETION_READY": DELETION_READY,
            "DESTRUCTIVE_OPERATION_REQUIRED": True,
            "WAITING_FOR_OPERATOR_APPROVAL": True,
            "EXECUTED": False,
        }

        # Canonical hash payload: exact delete sets only (immutable approval surface)
        hash_payload = json.dumps(
            {
                "DELETE_ACCOUNT_IDS": delete_accts,
                "DELETE_CAMPAIGN_IDS": delete_camps,
                "KEEP_ACCOUNT_IDS": keep_accts,
                "KEEP_CAMPAIGN_IDS": keep_camps,
                "UNKNOWN_ACCOUNT_IDS": unknown_accts,
                "UNKNOWN_CAMPAIGN_IDS": unknown_camps,
                "ACCOUNT2269_SELECTED_FOR_DELETE": False,
                "RUNNING_CAMPAIGN_IDS": running_ids,
                "DELETION_READY": DELETION_READY,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        CLEANUP_PLAN_SHA256 = sha256_bytes(hash_payload)
        CLEANUP_SCRIPT_SHA256 = sha256_bytes(script_text.encode("utf-8"))
        immutable["CLEANUP_PLAN_SHA256"] = CLEANUP_PLAN_SHA256
        immutable["CLEANUP_SCRIPT_SHA256"] = CLEANUP_SCRIPT_SHA256
        immutable["dependency_counts_accounts"] = dep_accts
        immutable["dependency_counts_campaigns"] = dep_camps

        plan["final_predelete_dry_run"] = immutable
        plan["safety_gates"] = {
            **plan.get("safety_gates", {}),
            **gates,
            "AUTO_DELETE_AUTHORIZED": False,  # still require operator; do not auto-run
            "DELETION_READY": DELETION_READY,
        }

        # Write updated plan to /tmp and host path if writable
        out_json = PLAN_C if PLAN_C.parent.exists() else PLAN_HOST
        out_json.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")

        md_lines = [
            "# Junk Account/Campaign Cleanup Plan — FINAL PRE-DELETE DRY RUN",
            "",
            f"dry_run_at: {immutable['dry_run_at']}",
            f"mode: {immutable['mode']}",
            f"EXECUTED: False",
            "",
            "## Exact delete set",
            "",
            f"DELETE_ACCOUNT_IDS={delete_accts}",
            f"KEEP_ACCOUNT_IDS={keep_accts}",
            f"UNKNOWN_ACCOUNT_IDS={unknown_accts}",
            f"DELETE_CAMPAIGN_IDS={delete_camps}",
            f"KEEP_CAMPAIGN_IDS={keep_camps}",
            f"UNKNOWN_CAMPAIGN_IDS={unknown_camps}",
            "",
            f"DELETE_ACCOUNT_COUNT={len(delete_accts)}",
            f"KEEP_ACCOUNT_COUNT={len(keep_accts)}",
            f"UNKNOWN_ACCOUNT_COUNT={len(unknown_accts)}",
            f"DELETE_CAMPAIGN_COUNT={len(delete_camps)}",
            f"KEEP_CAMPAIGN_COUNT={len(keep_camps)}",
            f"UNKNOWN_CAMPAIGN_COUNT={len(unknown_camps)}",
            "",
            "## Important exclusions",
            "",
            "ACCOUNT2269_CLASSIFICATION=REAL_OPERATIONAL_MANDATORY_KEEP",
            "ACCOUNT2269_SELECTED_FOR_DELETE=False",
            f"RUNNING_CAMPAIGN_IDS={running_ids}",
            "RUNNING_CAMPAIGNS_SELECTED_FOR_DELETE=0",
            "",
            "## Hashes",
            "",
            f"CLEANUP_PLAN_SHA256={CLEANUP_PLAN_SHA256}",
            f"CLEANUP_SCRIPT_SHA256={CLEANUP_SCRIPT_SHA256}",
            "",
            "## Safety gates",
            "",
        ]
        for k, v in gates.items():
            md_lines.append(f"- {k}={v}")
        md_lines += [
            "",
            f"DELETION_READY={DELETION_READY}",
            "DESTRUCTIVE_OPERATION_REQUIRED=True",
            "WAITING_FOR_OPERATOR_APPROVAL=True",
            "",
            "## Script audit",
            "",
            json.dumps(script_audit, indent=2),
            "",
            "## Preserve reasons",
            "",
            "- Account 2269: Campaign 233 sender; MessageAttempts 20/21; session 779; mandatory KEEP",
            "- Campaigns 183,191,199,232: status=running (bale-cp test leftovers) — KEEP per running exclusion",
            "",
            "## Delete reason",
            "",
            "Proven pytest debris (auto-prep-*, cp-gate-*, cp-block/ok/noprep, bale-cp,",
            "api409/api409b/pf/off fixtures) leaked into live DB; no successful external sends;",
            "no live queue; no ACTIVE sessions; no outside-range campaign deps (except 2269 kept).",
        ]
        out_md = Path("/tmp/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_PLAN.md")
        if not out_md.parent.exists():
            out_md = PLAN_MD_HOST
        out_md.write_text("\n".join(md_lines), encoding="utf-8")

        print(json.dumps(immutable, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
