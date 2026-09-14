"""Diff live DB vs R0 dump SQL extracts — READ ONLY."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal

REPORT = Path("/tmp/r10_forensic_out")
R0_EXTRACT = Path("/tmp/r0_extract")


def parse_copy(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    text_data = path.read_text(encoding="utf-8", errors="replace")
    rows: list[list[str]] = []
    in_copy = False
    for line in text_data.splitlines():
        if line.startswith("COPY "):
            in_copy = True
            continue
        if in_copy and (line == "\\." or line.startswith("\\.")):
            break
        if in_copy and line and not line.startswith("--"):
            rows.append(line.split("\t"))
    return rows


def main() -> None:
    # Expect extracts under reports/rubika-remediation/r0_extract/
    tables = {
        "accounts": "r0_accounts.sql",
        "campaigns": "r0_campaigns.sql",
        "contacts": "r0_contacts.sql",
        "messages": "r0_messages.sql",
        "staged_queue_items": "r0_staged.sql",
        "rubika_sender_schedules": "r0_schedules.sql",
        "rubika_account_pool": "r0_pool.sql",
        "channel_sessions": "r0_sessions.sql",
        "campaign_accounts": "r0_camp_acct.sql",
        "campaign_recipients": "r0_camp_recip.sql",
        "message_attempts": "r0_attempts.sql",
    }
    r0_ids: dict[str, set[int]] = {}
    r0_meta: dict[str, dict] = {}
    for table, fname in tables.items():
        rows = parse_copy(R0_EXTRACT / fname)
        ids = set()
        for row in rows:
            try:
                ids.add(int(row[0]))
            except Exception:
                continue
        r0_ids[table] = ids
        r0_meta[table] = {"count": len(ids), "max_id": max(ids) if ids else 0, "ids": sorted(ids)}

    db = SessionLocal()
    live: dict[str, dict] = {}
    try:
        for table in tables:
            rows = db.execute(text(f"SELECT id FROM {table} ORDER BY id")).fetchall()
            ids = {int(r[0]) for r in rows}
            only_live = sorted(ids - r0_ids[table])
            only_r0 = sorted(r0_ids[table] - ids)
            live[table] = {
                "live_count": len(ids),
                "r0_count": len(r0_ids[table]),
                "live_max": max(ids) if ids else 0,
                "r0_max": r0_meta[table]["max_id"],
                "ids_in_live_not_r0": only_live,
                "ids_in_r0_not_live": only_r0,
                "count_new": len(only_live),
                "count_missing_vs_r0": len(only_r0),
            }

        # Detailed new accounts
        new_acct_ids = live["accounts"]["ids_in_live_not_r0"]
        new_accounts = []
        if new_acct_ids:
            q = text(
                "SELECT id, platform::text, status::text, label, phone_number, created_at "
                "FROM accounts WHERE id = ANY(:ids) ORDER BY id"
            )
            for row in db.execute(q, {"ids": new_acct_ids}):
                phone = row.phone_number or ""
                digits = "".join(ch for ch in phone if ch.isdigit())
                masked = f"{digits[:3]}***{digits[-3:]}" if len(digits) >= 6 else "***"
                new_accounts.append(
                    {
                        "id": row.id,
                        "platform": row.platform,
                        "status": row.status,
                        "label": row.label,
                        "masked_phone": masked,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                )

        new_camp_ids = live["campaigns"]["ids_in_live_not_r0"]
        new_campaigns = []
        if new_camp_ids:
            q = text(
                "SELECT id, name, title, status, platform::text, channel, created_at, updated_at "
                "FROM campaigns WHERE id = ANY(:ids) ORDER BY id"
            )
            for row in db.execute(q, {"ids": new_camp_ids}):
                new_campaigns.append(
                    {
                        "id": row.id,
                        "name": row.name,
                        "title": row.title,
                        "status": row.status,
                        "platform": row.platform,
                        "channel": row.channel,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    }
                )

        # Schedules: compare active flags for shared IDs
        sched_diff = []
        q = text(
            "SELECT id, phase, is_active, start_hour, end_hour, max_per_hour, created_at, updated_at "
            "FROM rubika_sender_schedules ORDER BY id"
        )
        r0_sched_rows = parse_copy(R0_EXTRACT / "r0_schedules.sql")
        # COPY order: id, phase, start_hour, end_hour, max_per_hour, is_active, created_at, updated_at
        r0_sched = {}
        for row in r0_sched_rows:
            r0_sched[int(row[0])] = {
                "phase": row[1],
                "start_hour": int(row[2]),
                "end_hour": int(row[3]),
                "max_per_hour": int(row[4]),
                "is_active": row[5] in ("t", "true", "True"),
            }
        for row in db.execute(q):
            base = r0_sched.get(row.id)
            if base is None:
                sched_diff.append(
                    {
                        "id": row.id,
                        "kind": "NEW",
                        "phase": row.phase,
                        "is_active": row.is_active,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                )
            else:
                changes = {}
                if base["is_active"] != bool(row.is_active):
                    changes["is_active"] = {"r0": base["is_active"], "live": bool(row.is_active)}
                if base["phase"] != row.phase:
                    changes["phase"] = {"r0": base["phase"], "live": row.phase}
                if changes:
                    sched_diff.append(
                        {
                            "id": row.id,
                            "kind": "MODIFIED",
                            "phase": row.phase,
                            "changes": changes,
                            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                        }
                    )

        # Authorized accounts 12/79 session fingerprint (no secrets)
        db.rollback()
        sess_q2 = text(
            "SELECT id, account_id, session_type::text, created_at, updated_at "
            "FROM channel_sessions WHERE account_id IN (12,79) ORDER BY account_id, id"
        )
        sess_12_79 = [dict(r._mapping) for r in db.execute(sess_q2)]

        # Classify new accounts by label pattern
        test_label_re = re.compile(
            r"^(r22-|p6-|ok|other-global|auto-a|auto-b|redis-fail|healthy|sick|start-block|agg|window-wait|daily-cap|phase)",
            re.I,
        )
        classified = {"pytest_debris": [], "unknown": [], "production_looking": []}
        for a in new_accounts:
            label = a["label"] or ""
            if test_label_re.search(label) or label.lower() in {
                "ok",
                "healthy",
                "sick",
                "agg",
                "auto-a",
                "auto-b",
                "redis-fail",
                "other-global",
                "start-block",
                "window-wait",
                "daily-cap",
            }:
                classified["pytest_debris"].append(a)
            elif a["platform"] and "rubika" in a["platform"].lower():
                classified["unknown"].append(a)
            else:
                classified["production_looking"].append(a)

        out = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "r0_meta": {k: {"count": v["count"], "max_id": v["max_id"]} for k, v in r0_meta.items()},
            "diff_by_table": live,
            "new_accounts_detail": new_accounts,
            "new_accounts_classified": {
                k: {"count": len(v), "ids": [a["id"] for a in v], "sample_labels": list({a["label"] for a in v})[:30]}
                for k, v in classified.items()
            },
            "new_campaigns_detail": new_campaigns,
            "schedule_diff_vs_r0": sched_diff,
            "sessions_12_79": [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
                for row in sess_12_79
            ],
            "r10_session_facts": {
                "r10_dedicated_campaign": False,
                "r10_send_executed": False,
                "worker_stopped": True,
            },
        }
        REPORT.mkdir(parents=True, exist_ok=True)
        path = REPORT / "R10_R0_DIFF.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"WROTE {path}")
        print(json.dumps({
            "diff_counts": {t: live[t]["count_new"] for t in live},
            "classified": {k: v["count"] for k, v in out["new_accounts_classified"].items()},
            "new_campaigns": len(new_campaigns),
            "schedule_diff": sched_diff,
            "sessions_12_79_count": len(sess_12_79),
        }, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
