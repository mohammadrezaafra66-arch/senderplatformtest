"""READ-ONLY forensic inventory for accounts 2223-2311 and campaigns 128-232."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

from core_engine.database import SessionLocal, engine

ACCOUNT_LO, ACCOUNT_HI = 2223, 2311
CAMPAIGN_LO, CAMPAIGN_HI = 128, 232
MANDATORY_KEEP_ACCOUNTS = {2269}

TEST_LABEL_EXACT = {
    "ok",
    "other-global",
    "auto-a",
    "auto-b",
    "redis-fail",
    "healthy",
    "sick",
    "start-block",
    "agg",
    "window-wait",
    "daily-cap",
}
TEST_LABEL_PREFIXES = (
    "r22-",
    "p6",
    "pytest-",
    "test-",
    "l17-",
    "l18-",
    "iso-",
    "cp-test",
    "cp-",
    "e2e-",
    "hermetic-",
)
TEST_NAME_PREFIXES = (
    "r22-",
    "p6-",
    "pytest-",
    "test-",
    "auto-prepare-",
    "cp-gate-",
    "controlled-production-",
    "controlled production",
    "e2e-",
    "iso-",
    "l17-",
    "l18-",
    "hermetic-",
    "simulation-",
    "campaign auto",
    "ap-",
)


def mask_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def looks_test_label(label: str | None) -> bool:
    if not label:
        return False
    low = label.lower().strip()
    if low in TEST_LABEL_EXACT:
        return True
    return any(low.startswith(p) for p in TEST_LABEL_PREFIXES)


def looks_test_campaign_name(name: str | None) -> bool:
    if not name:
        return False
    low = name.lower().strip()
    return any(low.startswith(p) for p in TEST_NAME_PREFIXES)


def main() -> None:
    db = SessionLocal()
    out: dict = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "READ_ONLY_INVENTORY",
        "account_range": [ACCOUNT_LO, ACCOUNT_HI],
        "campaign_range": [CAMPAIGN_LO, CAMPAIGN_HI],
        "fk_map": {},
        "accounts": [],
        "campaigns": [],
        "provenance_summary": {},
    }
    try:
        insp = inspect(engine)
        for table in sorted(insp.get_table_names()):
            fks = []
            for fk in insp.get_foreign_keys(table):
                fks.append(
                    {
                        "constrained_columns": fk.get("constrained_columns"),
                        "referred_table": fk.get("referred_table"),
                        "referred_columns": fk.get("referred_columns"),
                        "options": fk.get("options") or {},
                    }
                )
            if fks:
                out["fk_map"][table] = fks

        accounts = db.execute(
            text(
                """
                SELECT a.id,
                       a.platform::text AS platform,
                       a.status::text AS status,
                       a.label,
                       a.phone_number,
                       a.created_at,
                       a.updated_at,
                       a.rubika_identity_status::text AS rubika_identity_status
                FROM accounts a
                WHERE a.id BETWEEN :lo AND :hi
                ORDER BY a.id
                """
            ),
            {"lo": ACCOUNT_LO, "hi": ACCOUNT_HI},
        ).mappings().all()

        existing_ids = [int(r["id"]) for r in accounts]
        missing_ids = [
            i for i in range(ACCOUNT_LO, ACCOUNT_HI + 1) if i not in set(existing_ids)
        ]

        for r in accounts:
            aid = int(r["id"])
            sess = db.execute(
                text(
                    """
                    SELECT count(*) AS n,
                           count(*) FILTER (
                             WHERE lower(session_status::text) IN
                               ('active','validating','legacy_unclassified')
                           ) AS present_n,
                           count(*) FILTER (
                             WHERE lower(session_status::text) = 'active'
                           ) AS active_n
                    FROM channel_sessions WHERE account_id=:aid
                    """
                ),
                {"aid": aid},
            ).mappings().one()
            camps = db.execute(
                text(
                    """
                    SELECT array_agg(DISTINCT campaign_id ORDER BY campaign_id)
                    FROM campaign_accounts WHERE account_id=:aid
                    """
                ),
                {"aid": aid},
            ).scalar()
            outside_camps = db.execute(
                text(
                    """
                    SELECT array_agg(DISTINCT campaign_id ORDER BY campaign_id)
                    FROM campaign_accounts
                    WHERE account_id=:aid
                      AND (campaign_id < :clo OR campaign_id > :chi)
                    """
                ),
                {"aid": aid, "clo": CAMPAIGN_LO, "chi": CAMPAIGN_HI},
            ).scalar()
            msg_n = db.execute(
                text("SELECT count(*) FROM messages WHERE account_id=:aid"),
                {"aid": aid},
            ).scalar()
            att = db.execute(
                text(
                    """
                    SELECT count(*) AS n,
                           count(*) FILTER (WHERE ma.status::text='SUCCESS') AS succ
                    FROM message_attempts ma
                    JOIN messages m ON m.id = ma.message_id
                    WHERE m.account_id=:aid
                    """
                ),
                {"aid": aid},
            ).mappings().one()
            queue_n = db.execute(
                text(
                    """
                    SELECT count(*) FROM staged_queue_items sq
                    WHERE sq.campaign_id IN (
                      SELECT campaign_id FROM campaign_accounts WHERE account_id=:aid
                    )
                    """
                ),
                {"aid": aid},
            ).scalar()
            live_queue = db.execute(
                text(
                    """
                    SELECT count(*) FROM staged_queue_items sq
                    WHERE sq.campaign_id IN (
                      SELECT campaign_id FROM campaign_accounts WHERE account_id=:aid
                    )
                    AND lower(sq.status::text) IN
                      ('queued','pending','reserved','processing','in_flight')
                    """
                ),
                {"aid": aid},
            ).scalar()
            audit_n = db.execute(
                text(
                    """
                    SELECT count(*) FROM audit_events
                    WHERE (subject_type ILIKE '%account%' AND subject_id = :aid_s)
                       OR payload::text LIKE :pat
                    """
                ),
                {"aid_s": str(aid), "pat": f'%"account_id": {aid}%'},
            ).scalar()
            audit_logs_n = db.execute(
                text(
                    """
                    SELECT count(*) FROM audit_logs
                    WHERE (resource_type ILIKE '%account%' AND resource_id = :aid_s)
                       OR details::text LIKE :pat
                    """
                ),
                {"aid_s": str(aid), "pat": f'%"account_id": {aid}%'},
            ).scalar()
            pool = db.execute(
                text(
                    """
                    SELECT id, phase::text AS phase, priority
                    FROM rubika_account_pool WHERE account_id=:aid
                    """
                ),
                {"aid": aid},
            ).mappings().all()
            sched = 0  # rubika_sender_schedules is phase-scoped, not account_id FK
            sent_reg = db.execute(
                text(
                    """
                    SELECT count(*) FROM rubika_global_sent_registry
                    WHERE last_sent_campaign_id IN (
                      SELECT campaign_id FROM campaign_accounts WHERE account_id=:aid
                    )
                    """
                ),
                {"aid": aid},
            ).scalar()
            login_ch = db.execute(
                text(
                    "SELECT count(*) FROM rubika_login_challenges WHERE account_id=:aid"
                ),
                {"aid": aid},
            ).scalar()
            rate_n = db.execute(
                text("SELECT count(*) FROM rate_policies WHERE account_id=:aid"),
                {"aid": aid},
            ).scalar()
            settings_n = db.execute(
                text(
                    "SELECT count(*) FROM account_send_settings WHERE account_id=:aid"
                ),
                {"aid": aid},
            ).scalar()

            label = r["label"]
            out["accounts"].append(
                {
                    "ACCOUNT_ID": aid,
                    "PLATFORM": r["platform"],
                    "DISPLAY_IDENTITY": (label or "")[:80] or None,
                    "PHONE_MASKED": mask_phone(r["phone_number"]),
                    "CREATED_AT": r["created_at"].isoformat() if r["created_at"] else None,
                    "UPDATED_AT": r["updated_at"].isoformat() if r["updated_at"] else None,
                    "STATUS": r["status"],
                    "RUBIKA_IDENTITY_STATUS": r["rubika_identity_status"],
                    "SESSION_COUNT": int(sess["n"] or 0),
                    "ACTIVE_SESSION_COUNT": int(sess["active_n"] or 0),
                    "PRESENT_SESSION_COUNT": int(sess["present_n"] or 0),
                    "CAMPAIGN_REFERENCES": list(camps or []) if camps else [],
                    "OUTSIDE_RANGE_CAMPAIGN_REFERENCES": list(outside_camps or [])
                    if outside_camps
                    else [],
                    "MESSAGE_REFERENCES": int(msg_n or 0),
                    "MESSAGE_ATTEMPT_REFERENCES": int(att["n"] or 0),
                    "SUCCESS_ATTEMPT_COUNT": int(att["succ"] or 0),
                    "QUEUE_REFERENCES": int(queue_n or 0),
                    "LIVE_QUEUE_REFERENCES": int(live_queue or 0),
                    "AUDIT_REFERENCES": int(audit_n or 0) + int(audit_logs_n or 0),
                    "WORKER_BINDING": {
                        "pool_rows": [dict(p) for p in pool],
                        "schedule_count": int(sched or 0),
                    },
                    "OTHER_DEPENDENCIES": {
                        "rubika_global_sent_registry": int(sent_reg or 0),
                        "rubika_login_challenges": int(login_ch or 0),
                        "rate_policies": int(rate_n or 0),
                        "account_send_settings": int(settings_n or 0),
                    },
                    "LABEL_LOOKS_TEST": looks_test_label(label),
                    "MANDATORY_KEEP": aid in MANDATORY_KEEP_ACCOUNTS,
                }
            )

        out["accounts_missing_in_range"] = missing_ids
        out["accounts_existing_count"] = len(accounts)

        camps = db.execute(
            text(
                """
                SELECT c.id, c.name, c.title, c.status::text AS status,
                       c.platform::text AS platform, c.channel,
                       c.created_at, c.updated_at, c.schedule_start_at
                FROM campaigns c
                WHERE c.id BETWEEN :lo AND :hi
                ORDER BY c.id
                """
            ),
            {"lo": CAMPAIGN_LO, "hi": CAMPAIGN_HI},
        ).mappings().all()

        existing_cids = [int(r["id"]) for r in camps]
        missing_cids = [
            i for i in range(CAMPAIGN_LO, CAMPAIGN_HI + 1) if i not in set(existing_cids)
        ]

        for r in camps:
            cid = int(r["id"])
            recip = db.execute(
                text(
                    "SELECT count(*) FROM campaign_recipients WHERE campaign_id=:cid"
                ),
                {"cid": cid},
            ).scalar()
            msgs = db.execute(
                text("SELECT count(*) FROM messages WHERE campaign_id=:cid"),
                {"cid": cid},
            ).scalar()
            rendered = db.execute(
                text(
                    "SELECT count(*) FROM rendered_messages WHERE campaign_id=:cid"
                ),
                {"cid": cid},
            ).scalar()
            assigned = db.execute(
                text(
                    """
                    SELECT array_agg(DISTINCT account_id ORDER BY account_id)
                    FROM campaign_accounts WHERE campaign_id=:cid
                    """
                ),
                {"cid": cid},
            ).scalar()
            att = db.execute(
                text(
                    """
                    SELECT count(*) AS n,
                           count(*) FILTER (WHERE ma.status::text='SUCCESS') AS succ,
                           count(*) FILTER (WHERE ma.status::text ILIKE '%FAIL%') AS fail
                    FROM message_attempts ma
                    JOIN messages m ON m.id = ma.message_id
                    WHERE m.campaign_id=:cid
                    """
                ),
                {"cid": cid},
            ).mappings().one()
            queue = db.execute(
                text(
                    """
                    SELECT count(*) AS n,
                           count(*) FILTER (
                             WHERE lower(sq.status::text) IN
                               ('queued','pending','reserved','processing','in_flight')
                           ) AS live
                    FROM staged_queue_items sq
                    WHERE sq.campaign_id=:cid
                    """
                ),
                {"cid": cid},
            ).mappings().one()
            ext = db.execute(
                text(
                    """
                    SELECT count(*) FROM message_attempts ma
                    JOIN messages m ON m.id = ma.message_id
                    WHERE m.campaign_id=:cid
                      AND ma.status::text='SUCCESS'
                      AND ma.platform_message_id IS NOT NULL
                      AND ma.platform_message_id NOT LIKE 'dry-%'
                      AND ma.platform_message_id NOT LIKE 'shadow-%'
                      AND ma.platform_message_id <> 'x'
                    """
                ),
                {"cid": cid},
            ).scalar()
            audit_c = db.execute(
                text(
                    """
                    SELECT count(*) FROM audit_events
                    WHERE (subject_type ILIKE '%campaign%' AND subject_id = :cid_s)
                       OR payload::text LIKE :pat
                    """
                ),
                {"cid_s": str(cid), "pat": f'%"campaign_id": {cid}%'},
            ).scalar()
            name = r["name"] or r["title"] or ""
            out["campaigns"].append(
                {
                    "CAMPAIGN_ID": cid,
                    "NAME": name[:120],
                    "CREATED_AT": r["created_at"].isoformat() if r["created_at"] else None,
                    "UPDATED_AT": r["updated_at"].isoformat() if r["updated_at"] else None,
                    "STATUS": r["status"],
                    "PLATFORM": r["platform"],
                    "CHANNEL": r["channel"],
                    "RECIPIENT_COUNT": int(recip or 0),
                    "FINAL_MESSAGE_COUNT": int(msgs or 0),
                    "RENDERED_MESSAGE_COUNT": int(rendered or 0),
                    "ASSIGNED_ACCOUNT_IDS": list(assigned or []) if assigned else [],
                    "MESSAGE_ATTEMPT_COUNT": int(att["n"] or 0),
                    "SUCCESS_ATTEMPT_COUNT": int(att["succ"] or 0),
                    "FAILED_ATTEMPT_COUNT": int(att["fail"] or 0),
                    "QUEUE_RECORD_COUNT": int(queue["n"] or 0),
                    "STAGED_QUEUE_LIVE_COUNT": int(queue["live"] or 0),
                    "AUDIT_COUNT": int(audit_c or 0),
                    "STARTED_AT": r["schedule_start_at"].isoformat()
                    if r["schedule_start_at"]
                    else None,
                    "COMPLETED_AT": None,
                    "EXTERNAL_SEND_EVIDENCE": int(ext or 0),
                    "NAME_LOOKS_TEST": looks_test_campaign_name(name),
                    "IS_RUNNING": (r["status"] or "").lower()
                    in {"running", "sending", "active"},
                }
            )

        out["campaigns_missing_in_range"] = missing_cids
        out["campaigns_existing_count"] = len(camps)

        label_c = Counter(
            ((a["DISPLAY_IDENTITY"] or "NULL").split("-")[0])[:40] for a in out["accounts"]
        )
        name_c = Counter((c["NAME"] or "NULL")[:60] for c in out["campaigns"])
        day_a = Counter((a["CREATED_AT"] or "")[:10] for a in out["accounts"])
        day_c = Counter((c["CREATED_AT"] or "")[:10] for c in out["campaigns"])
        status_a = Counter(a["STATUS"] for a in out["accounts"])
        status_c = Counter(c["STATUS"] for c in out["campaigns"])

        out["provenance_summary"] = {
            "account_label_prefix_counts": label_c.most_common(40),
            "campaign_name_counts": name_c.most_common(50),
            "account_created_day_counts": day_a.most_common(),
            "campaign_created_day_counts": day_c.most_common(),
            "account_status_counts": status_a.most_common(),
            "campaign_status_counts": status_c.most_common(),
            "accounts_label_looks_test": sum(
                1 for a in out["accounts"] if a["LABEL_LOOKS_TEST"]
            ),
            "campaigns_name_looks_test": sum(
                1 for c in out["campaigns"] if c["NAME_LOOKS_TEST"]
            ),
            "accounts_with_success_attempts": [
                a["ACCOUNT_ID"] for a in out["accounts"] if a["SUCCESS_ATTEMPT_COUNT"] > 0
            ],
            "accounts_with_outside_campaigns": [
                {
                    "account_id": a["ACCOUNT_ID"],
                    "campaigns": a["OUTSIDE_RANGE_CAMPAIGN_REFERENCES"],
                }
                for a in out["accounts"]
                if a["OUTSIDE_RANGE_CAMPAIGN_REFERENCES"]
            ],
            "accounts_with_sessions": [
                a["ACCOUNT_ID"] for a in out["accounts"] if a["SESSION_COUNT"] > 0
            ],
            "campaigns_with_external_send": [
                c["CAMPAIGN_ID"] for c in out["campaigns"] if c["EXTERNAL_SEND_EVIDENCE"] > 0
            ],
            "campaigns_running": [
                c["CAMPAIGN_ID"] for c in out["campaigns"] if c["IS_RUNNING"]
            ],
            "campaigns_with_attempts": [
                c["CAMPAIGN_ID"]
                for c in out["campaigns"]
                if c["MESSAGE_ATTEMPT_COUNT"] > 0
            ],
        }

        create_audits = db.execute(
            text(
                """
                SELECT id, event_type, actor, subject_type, subject_id, created_at,
                       left(coalesce(payload::text,''), 180) AS payload_head
                FROM audit_events
                WHERE subject_type ILIKE '%campaign%'
                  AND subject_id ~ '^[0-9]+$'
                  AND subject_id::int BETWEEN :lo AND :hi
                ORDER BY id
                LIMIT 300
                """
            ),
            {"lo": CAMPAIGN_LO, "hi": CAMPAIGN_HI},
        ).mappings().all()
        out["campaign_audit_sample"] = [dict(x) for x in create_audits]
        out["campaign_audit_event_types"] = Counter(
            x["event_type"] for x in create_audits
        ).most_common()

        acct_audits = db.execute(
            text(
                """
                SELECT id, event_type, actor, subject_type, subject_id, created_at,
                       left(coalesce(payload::text,''), 180) AS payload_head
                FROM audit_events
                WHERE subject_type ILIKE '%account%'
                  AND subject_id ~ '^[0-9]+$'
                  AND subject_id::int BETWEEN :lo AND :hi
                ORDER BY id
                LIMIT 300
                """
            ),
            {"lo": ACCOUNT_LO, "hi": ACCOUNT_HI},
        ).mappings().all()
        out["account_audit_sample"] = [dict(x) for x in acct_audits]
        out["account_audit_event_types"] = Counter(
            x["event_type"] for x in acct_audits
        ).most_common()

        path = Path("/tmp/junk_range_inventory.json")
        path.write_text(json.dumps(out, default=str, indent=2), encoding="utf-8")
        print("WROTE", path)
        print("ACCOUNTS_EXISTING", out["accounts_existing_count"])
        print("ACCOUNTS_MISSING", len(missing_ids))
        print("CAMPAIGNS_EXISTING", out["campaigns_existing_count"])
        print("CAMPAIGNS_MISSING", len(missing_cids))
        print(json.dumps(out["provenance_summary"], default=str, indent=2)[:5000])
    finally:
        db.close()


if __name__ == "__main__":
    main()
