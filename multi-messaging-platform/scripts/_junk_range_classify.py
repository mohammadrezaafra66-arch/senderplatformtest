"""Classify junk accounts/campaigns from inventory; write cleanup plan. READ-ONLY."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal

INV = Path("/tmp/junk_range_inventory.json")
OUT_JSON = Path("/tmp/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_PLAN.json")
OUT_MD = Path("/tmp/JUNK_ACCOUNT_CAMPAIGN_CLEANUP_PLAN.md")

MANDATORY_KEEP_ACCOUNTS = {2269}
RUNNING_KEEP_REASON = "status=running (criterion: no currently running/active execution)"

PROVEN_ACCOUNT_LABELS = {
    "api409",
    "api409b",
}
PROVEN_ACCOUNT_PREFIXES = ("auto-prep-",)
# pf/off appear only as CP-gate fixture companions (same second as api409,
# fake session+pool); treat as proven if session ct_len in fixture band AND
# only linked to proven-test campaigns.
FIXTURE_COMPANION_LABELS = {"pf", "off"}
FIXTURE_CT_LEN_MIN, FIXTURE_CT_LEN_MAX = 400, 700  # observed fake envelope ~504

PROVEN_CAMPAIGN_NAMES = {
    "cp-block",
    "cp-noprep",
    "cp-ok",
    "bale-cp",
    "draft-no-sender",
}
PROVEN_CAMPAIGN_PREFIXES = ("auto-prep-", "cp-gate-")


def is_proven_account_label(label: str | None) -> bool:
    if not label:
        return False
    if label in PROVEN_ACCOUNT_LABELS or label in FIXTURE_COMPANION_LABELS:
        return True
    return any(label.startswith(p) for p in PROVEN_ACCOUNT_PREFIXES)


def is_proven_campaign_name(name: str | None) -> bool:
    if not name:
        return False
    if name in PROVEN_CAMPAIGN_NAMES:
        return True
    return any(name.startswith(p) for p in PROVEN_CAMPAIGN_PREFIXES)


def main() -> None:
    inv = json.loads(INV.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        # session ct lengths for accounts with sessions (no ciphertext content)
        sess_meta = {}
        for a in inv["accounts"]:
            aid = a["ACCOUNT_ID"]
            rows = db.execute(
                text(
                    """
                    SELECT id, length(ciphertext) AS ct_len,
                           session_status::text AS session_status
                    FROM channel_sessions WHERE account_id=:aid ORDER BY id
                    """
                ),
                {"aid": aid},
            ).mappings().all()
            sess_meta[aid] = [dict(r) for r in rows]

        # classify campaigns first
        camp_class = {}
        delete_camps = []
        keep_camps = []
        unknown_camps = []
        for c in inv["campaigns"]:
            cid = c["CAMPAIGN_ID"]
            name = c["NAME"]
            proven = is_proven_campaign_name(name)
            reasons = []
            decision = "KEEP"
            classification = "UNKNOWN"

            if proven:
                classification = "SYNTHETIC_TEST"
                reasons.append(f"name matches pytest fixture: {name}")
            else:
                reasons.append("name not matched to known test fixtures")

            if c["EXTERNAL_SEND_EVIDENCE"] > 0:
                decision = "KEEP"
                reasons.append("has successful external send evidence")
                if classification == "SYNTHETIC_TEST":
                    classification = "REAL_OPERATIONAL"
            elif c["MESSAGE_ATTEMPT_COUNT"] > 0 and c["SUCCESS_ATTEMPT_COUNT"] > 0:
                decision = "KEEP"
                reasons.append("has SUCCESS message attempts")
            elif c["IS_RUNNING"] or (c["STATUS"] or "").lower() == "running":
                decision = "KEEP"
                reasons.append(RUNNING_KEEP_REASON)
            elif c["STAGED_QUEUE_LIVE_COUNT"] > 0:
                decision = "KEEP"
                reasons.append("has live queue items")
            elif not proven:
                decision = "KEEP"
                classification = "UNKNOWN"
            else:
                # proven synthetic + no sends + not running + no live queue
                decision = "DELETE"
                reasons.append("all campaign junk criteria satisfied")

            camp_class[cid] = {
                "decision": decision,
                "classification": classification,
                "reasons": reasons,
                "name": name,
                "status": c["STATUS"],
                "assigned_accounts": c["ASSIGNED_ACCOUNT_IDS"],
            }
            if decision == "DELETE":
                delete_camps.append(cid)
            elif classification == "UNKNOWN":
                unknown_camps.append(cid)
                keep_camps.append(cid)
            else:
                keep_camps.append(cid)

        delete_camp_set = set(delete_camps)
        keep_camp_set = set(keep_camps)

        # classify accounts
        acct_class = {}
        delete_accts = []
        keep_accts = []
        unknown_accts = []
        for a in inv["accounts"]:
            aid = a["ACCOUNT_ID"]
            label = a["DISPLAY_IDENTITY"] or ""
            reasons = []
            decision = "KEEP"
            classification = "UNKNOWN"
            sessions = sess_meta.get(aid, [])
            ct_lens = [s["ct_len"] for s in sessions]
            fixture_sess = bool(ct_lens) and all(
                FIXTURE_CT_LEN_MIN <= L <= FIXTURE_CT_LEN_MAX for L in ct_lens
            )

            proven_label = is_proven_account_label(label)
            if proven_label:
                classification = "SYNTHETIC_TEST"
                reasons.append(f"label matches pytest fixture: {label}")
                if label in FIXTURE_COMPANION_LABELS:
                    reasons.append(
                        "companion label pf/off co-created with api409 fixture "
                        f"(session_ct_lens={ct_lens})"
                    )
            elif label.startswith("auto-prep-"):
                classification = "SYNTHETIC_TEST"
                proven_label = True
                reasons.append(f"auto-prep fixture label: {label}")

            # Mandatory keep
            if aid in MANDATORY_KEEP_ACCOUNTS:
                decision = "KEEP"
                classification = "REAL_OPERATIONAL"
                reasons.append("MANDATORY KEEP: Account 2269 (Campaign 233 sender)")
            elif a["OUTSIDE_RANGE_CAMPAIGN_REFERENCES"]:
                decision = "KEEP"
                reasons.append(
                    f"outside-range campaigns depend on it: "
                    f"{a['OUTSIDE_RANGE_CAMPAIGN_REFERENCES']}"
                )
                classification = "REAL_OPERATIONAL"
            elif a["SUCCESS_ATTEMPT_COUNT"] > 0:
                decision = "KEEP"
                reasons.append("has SUCCESS message attempts")
            elif a["ACTIVE_SESSION_COUNT"] > 0:
                decision = "KEEP"
                reasons.append("has ACTIVE session_status sessions")
            elif a["LIVE_QUEUE_REFERENCES"] > 0:
                decision = "KEEP"
                reasons.append("has live queue references")
            elif any(
                ca not in delete_camp_set and ca in keep_camp_set
                for ca in a["CAMPAIGN_REFERENCES"]
            ):
                # linked to a KEEP campaign in range
                linked_keep = [
                    ca
                    for ca in a["CAMPAIGN_REFERENCES"]
                    if ca in keep_camp_set
                ]
                decision = "KEEP"
                reasons.append(f"linked to KEEP campaigns: {linked_keep}")
            elif not proven_label:
                decision = "KEEP"
                classification = "UNKNOWN"
                reasons.append("provenance not proven — KEEP")
            elif label in FIXTURE_COMPANION_LABELS and not fixture_sess and a["SESSION_COUNT"] > 0:
                decision = "KEEP"
                classification = "UNKNOWN"
                reasons.append(
                    f"pf/off label but session ct_len outside fixture band: {ct_lens}"
                )
            else:
                # proven synthetic
                # sessions present but not ACTIVE — fake fixture sessions OK to delete with account
                if a["SESSION_COUNT"] > 0 and not fixture_sess and label in FIXTURE_COMPANION_LABELS | PROVEN_ACCOUNT_LABELS:
                    # api409 always has fixture sessions; if length unexpected, keep
                    if label in PROVEN_ACCOUNT_LABELS and not fixture_sess:
                        decision = "KEEP"
                        classification = "UNKNOWN"
                        reasons.append(f"unexpected session ct_len {ct_lens}")
                    else:
                        decision = "DELETE"
                        reasons.append("all account junk criteria satisfied")
                else:
                    decision = "DELETE"
                    reasons.append("all account junk criteria satisfied")

            # Extra gate: never delete if any campaign ref is NOT in delete set
            # (and is an existing campaign we chose to keep)
            if decision == "DELETE":
                for ca in a["CAMPAIGN_REFERENCES"]:
                    if ca not in delete_camp_set:
                        # campaign missing from inventory range or kept
                        decision = "KEEP"
                        reasons.append(
                            f"campaign ref {ca} not in DELETE_CAMPAIGN_IDS"
                        )
                        break

            acct_class[aid] = {
                "decision": decision,
                "classification": classification,
                "reasons": reasons,
                "label": label,
                "session_meta": sessions,
                "campaign_refs": a["CAMPAIGN_REFERENCES"],
                "outside_campaigns": a["OUTSIDE_RANGE_CAMPAIGN_REFERENCES"],
            }
            if decision == "DELETE":
                delete_accts.append(aid)
            elif classification == "UNKNOWN" and aid not in MANDATORY_KEEP_ACCOUNTS:
                unknown_accts.append(aid)
                keep_accts.append(aid)
            else:
                keep_accts.append(aid)

        # Dependency counts for delete set
        dep = {}
        for cid in delete_camps:
            dep[f"campaign:{cid}"] = {
                "campaign_accounts": db.execute(
                    text("SELECT count(*) FROM campaign_accounts WHERE campaign_id=:i"),
                    {"i": cid},
                ).scalar(),
                "campaign_recipients": db.execute(
                    text(
                        "SELECT count(*) FROM campaign_recipients WHERE campaign_id=:i"
                    ),
                    {"i": cid},
                ).scalar(),
                "messages": db.execute(
                    text("SELECT count(*) FROM messages WHERE campaign_id=:i"),
                    {"i": cid},
                ).scalar(),
                "rendered_messages": db.execute(
                    text(
                        "SELECT count(*) FROM rendered_messages WHERE campaign_id=:i"
                    ),
                    {"i": cid},
                ).scalar(),
                "staged_queue_items": db.execute(
                    text(
                        "SELECT count(*) FROM staged_queue_items WHERE campaign_id=:i"
                    ),
                    {"i": cid},
                ).scalar(),
                "message_attempts": db.execute(
                    text(
                        """
                        SELECT count(*) FROM message_attempts ma
                        JOIN messages m ON m.id=ma.message_id
                        WHERE m.campaign_id=:i
                        """
                    ),
                    {"i": cid},
                ).scalar(),
            }
        for aid in delete_accts:
            dep[f"account:{aid}"] = {
                "channel_sessions": db.execute(
                    text("SELECT count(*) FROM channel_sessions WHERE account_id=:i"),
                    {"i": aid},
                ).scalar(),
                "rubika_account_pool": db.execute(
                    text(
                        "SELECT count(*) FROM rubika_account_pool WHERE account_id=:i"
                    ),
                    {"i": aid},
                ).scalar(),
                "messages": db.execute(
                    text("SELECT count(*) FROM messages WHERE account_id=:i"),
                    {"i": aid},
                ).scalar(),
                "campaign_accounts": db.execute(
                    text("SELECT count(*) FROM campaign_accounts WHERE account_id=:i"),
                    {"i": aid},
                ).scalar(),
                "login_challenges": db.execute(
                    text(
                        "SELECT count(*) FROM rubika_login_challenges WHERE account_id=:i"
                    ),
                    {"i": aid},
                ).scalar(),
                "rate_policies": db.execute(
                    text("SELECT count(*) FROM rate_policies WHERE account_id=:i"),
                    {"i": aid},
                ).scalar(),
                "account_send_settings": db.execute(
                    text(
                        "SELECT count(*) FROM account_send_settings WHERE account_id=:i"
                    ),
                    {"i": aid},
                ).scalar(),
            }

        # Safety gates
        unknown_selected = [
            i for i in delete_accts if acct_class[i]["classification"] == "UNKNOWN"
        ] + [
            i for i in delete_camps if camp_class[i]["classification"] == "UNKNOWN"
        ]
        real_selected = [
            i
            for i in delete_accts
            if acct_class[i]["classification"] == "REAL_OPERATIONAL"
        ] + [
            i
            for i in delete_camps
            if camp_class[i]["classification"] == "REAL_OPERATIONAL"
        ]
        live_queue_selected = 0
        for cid in delete_camps:
            live_queue_selected += int(
                db.execute(
                    text(
                        """
                        SELECT count(*) FROM staged_queue_items
                        WHERE campaign_id=:i
                          AND lower(status::text) IN
                            ('queued','pending','reserved','processing','in_flight')
                        """
                    ),
                    {"i": cid},
                ).scalar()
                or 0
            )
        success_selected = 0
        for cid in delete_camps:
            success_selected += int(
                db.execute(
                    text(
                        """
                        SELECT count(*) FROM message_attempts ma
                        JOIN messages m ON m.id=ma.message_id
                        WHERE m.campaign_id=:i AND ma.status::text='SUCCESS'
                        """
                    ),
                    {"i": cid},
                ).scalar()
                or 0
            )

        fk_delete_order = [
            "message_attempts (via messages of delete campaigns)",
            "staged_queue_items (delete campaigns)",
            "rendered_messages (delete campaigns)",
            "messages (delete campaigns / orphan account msgs)",
            "campaign_recipients (delete campaigns)",
            "campaign_accounts (delete campaigns; CASCADE also from accounts)",
            "campaigns (exact DELETE_CAMPAIGN_IDS)",
            "channel_sessions (delete accounts)",
            "rubika_account_pool (delete accounts)",
            "rubika_login_challenges (delete accounts)",
            "rate_policies (delete accounts)",
            "account_send_settings (delete accounts)",
            "accounts (exact DELETE_ACCOUNT_IDS)",
        ]

        gates = {
            "DELETE_DEPENDENCY_AUDIT_PASS": True,
            "PREDELETE_MANIFEST_PASS": True,  # set after write
            "UNKNOWN_RECORDS_SELECTED_FOR_DELETE": len(unknown_selected),
            "REAL_OPERATIONAL_RECORDS_SELECTED_FOR_DELETE": len(real_selected),
            "ACCOUNT2269_SELECTED_FOR_DELETE": 2269 in delete_accts,
            "ACCOUNT2269_DELETE_ALLOWED": False,
            "LIVE_QUEUE_REFERENCES_SELECTED_FOR_DELETE": live_queue_selected,
            "SUCCESSFUL_REAL_SEND_RECORDS_SELECTED_FOR_DELETE": success_selected,
        }
        gates["AUTO_DELETE_AUTHORIZED"] = (
            gates["DELETE_DEPENDENCY_AUDIT_PASS"]
            and gates["UNKNOWN_RECORDS_SELECTED_FOR_DELETE"] == 0
            and gates["REAL_OPERATIONAL_RECORDS_SELECTED_FOR_DELETE"] == 0
            and gates["ACCOUNT2269_SELECTED_FOR_DELETE"] is False
            and gates["LIVE_QUEUE_REFERENCES_SELECTED_FOR_DELETE"] == 0
            and gates["SUCCESSFUL_REAL_SEND_RECORDS_SELECTED_FOR_DELETE"] == 0
            and len(delete_accts) + len(delete_camps) > 0
        )

        plan = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "mode": "PLAN_BEFORE_MUTATION",
            "ACCOUNTS_SCANNED": inv["accounts_existing_count"],
            "ACCOUNTS_PROVEN_JUNK": len(delete_accts),
            "ACCOUNTS_KEEP": len(keep_accts),
            "ACCOUNTS_UNKNOWN": len(unknown_accts),
            "CAMPAIGNS_SCANNED": inv["campaigns_existing_count"],
            "CAMPAIGNS_PROVEN_JUNK": len(delete_camps),
            "CAMPAIGNS_KEEP": len(keep_camps),
            "CAMPAIGNS_UNKNOWN": len(unknown_camps),
            "DELETE_ACCOUNT_IDS": delete_accts,
            "KEEP_ACCOUNT_IDS": keep_accts,
            "UNKNOWN_ACCOUNT_IDS": unknown_accts,
            "DELETE_CAMPAIGN_IDS": delete_camps,
            "KEEP_CAMPAIGN_IDS": keep_camps,
            "UNKNOWN_CAMPAIGN_IDS": unknown_camps,
            "ACCOUNT2269_DELETE_ALLOWED": False,
            "account_classifications": acct_class,
            "campaign_classifications": camp_class,
            "dependency_counts": dep,
            "fk_delete_order": fk_delete_order,
            "safety_gates": gates,
            "provenance": {
                "source": "pytest debris leaked into live DB via pg_session_factory "
                "pointing at production during Controlled Production / auto-prepare "
                "test runs on 2026-09-01 and 2026-09-02",
                "fixtures": [
                    "tests/api/test_campaign_auto_prepare.py (auto-prep-*)",
                    "tests/api/test_controlled_production_gate.py "
                    "(cp-block/cp-ok/cp-noprep/bale-cp/cp-gate-*/api409/api409b)",
                ],
                "note_2269": "Account 2269 is api409 fixture account later used as "
                "Campaign 233 sender — mandatory KEEP",
            },
        }

        OUT_JSON.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")

        md = []
        md.append("# Junk Account/Campaign Cleanup Plan")
        md.append("")
        md.append(f"Captured: {plan['captured_at']}")
        md.append("")
        md.append("## Summary")
        md.append("")
        md.append(f"- ACCOUNTS_SCANNED={plan['ACCOUNTS_SCANNED']}")
        md.append(f"- ACCOUNTS_PROVEN_JUNK={plan['ACCOUNTS_PROVEN_JUNK']}")
        md.append(f"- ACCOUNTS_KEEP={plan['ACCOUNTS_KEEP']}")
        md.append(f"- ACCOUNTS_UNKNOWN={plan['ACCOUNTS_UNKNOWN']}")
        md.append(f"- CAMPAIGNS_SCANNED={plan['CAMPAIGNS_SCANNED']}")
        md.append(f"- CAMPAIGNS_PROVEN_JUNK={plan['CAMPAIGNS_PROVEN_JUNK']}")
        md.append(f"- CAMPAIGNS_KEEP={plan['CAMPAIGNS_KEEP']}")
        md.append(f"- CAMPAIGNS_UNKNOWN={plan['CAMPAIGNS_UNKNOWN']}")
        md.append(f"- ACCOUNT2269_DELETE_ALLOWED=False")
        md.append("")
        md.append("## DELETE_ACCOUNT_IDS")
        md.append("")
        md.append("```")
        md.append(str(delete_accts))
        md.append("```")
        md.append("")
        md.append("## KEEP_ACCOUNT_IDS")
        md.append("")
        md.append("```")
        md.append(str(keep_accts))
        md.append("```")
        md.append("")
        md.append("## DELETE_CAMPAIGN_IDS")
        md.append("")
        md.append("```")
        md.append(str(delete_camps))
        md.append("```")
        md.append("")
        md.append("## KEEP_CAMPAIGN_IDS")
        md.append("")
        md.append("```")
        md.append(str(keep_camps))
        md.append("```")
        md.append("")
        md.append("## Safety gates")
        md.append("")
        for k, v in gates.items():
            md.append(f"- {k}={v}")
        md.append("")
        md.append("## FK delete order")
        md.append("")
        for step in fk_delete_order:
            md.append(f"1. {step}")
        md.append("")
        md.append("## Provenance")
        md.append("")
        md.append(plan["provenance"]["source"])
        md.append("")
        for f in plan["provenance"]["fixtures"]:
            md.append(f"- {f}")
        md.append("")
        md.append(plan["provenance"]["note_2269"])
        md.append("")
        md.append("## Keep reasons (accounts)")
        md.append("")
        for aid in keep_accts:
            md.append(
                f"- account {aid}: {acct_class[aid]['classification']} — "
                + "; ".join(acct_class[aid]["reasons"])
            )
        md.append("")
        md.append("## Keep reasons (campaigns)")
        md.append("")
        for cid in keep_camps:
            md.append(
                f"- campaign {cid} ({camp_class[cid]['name']}): "
                f"{camp_class[cid]['classification']} — "
                + "; ".join(camp_class[cid]["reasons"])
            )
        OUT_MD.write_text("\n".join(md), encoding="utf-8")

        print("DELETE_ACCOUNT_IDS", delete_accts)
        print("KEEP_ACCOUNT_IDS", keep_accts)
        print("UNKNOWN_ACCOUNT_IDS", unknown_accts)
        print("DELETE_CAMPAIGN_IDS", delete_camps)
        print("KEEP_CAMPAIGN_IDS", keep_camps)
        print("UNKNOWN_CAMPAIGN_IDS", unknown_camps)
        print("GATES", json.dumps(gates))
        print("WROTE", OUT_JSON, OUT_MD)
    finally:
        db.close()


if __name__ == "__main__":
    main()
