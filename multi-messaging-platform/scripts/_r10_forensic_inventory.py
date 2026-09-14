"""R10 forensic inventory — READ ONLY. No deletes, no sends, no OTP."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

from core_engine.database import SessionLocal, engine
from core_engine.models import (
    Account,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    ChannelSession,
    Contact,
    Message,
    MessageAttempt,
    PlatformType,
    RubikaAccountPool,
    RubikaSenderSchedule,
    StagedQueueItem,
)

# Remediation epoch (R0 backup wall clock on host).
R0_CUTOVER = datetime(2026, 8, 26, 12, 42, 59)  # UTC approx of 16:12:59 +0330
# Local R3–R10 coding window (2026-08-29 morning Iran time → UTC).
R3_R10_WINDOW_START = datetime(2026, 8, 29, 6, 0, 0)  # ~09:30 +0330

REPORT_DIR = Path("/app/reports/rubika-remediation")
OUT_JSON = REPORT_DIR / "R10_CREATED_DATA_FORENSIC_INVENTORY.json"


def _iso(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.isoformat()


def _after(dt, cutoff):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    return dt >= cutoff


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"


def _table_columns(table: str) -> set[str]:
    insp = inspect(engine)
    return {c["name"] for c in insp.get_columns(table)}


def main() -> None:
    db = SessionLocal()
    out: dict = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "anchors": {
            "r0_backup": "C:/Users/poorchista/senderplatform-r0-backups/20260826_161259/mmp_postgres_r0_3.dump",
            "r0_sha256": "4f41279a7bf5c2b54e91e8fd36f920185219d5c32aa825b14bf9cd76864f2c05",
            "r0_cutover_naive_utc_approx": R0_CUTOVER.isoformat(),
            "r3_r10_window_start_utc": R3_R10_WINDOW_START.isoformat(),
            "authorized_r10_accounts": [12, 79],
            "authorized_r10_contacts": [2, 92],
            "note": "Naive DB timestamps are compared as naive UTC-approx; PostgreSQL stores without tz.",
        },
        "tables": {},
        "summary": {},
        "suspicious_heuristics": {},
    }
    try:
        # ── accounts ──────────────────────────────────────────────
        accounts = db.query(Account).order_by(Account.id.asc()).all()
        acct_rows = []
        for a in accounts:
            created = getattr(a, "created_at", None)
            row = {
                "id": a.id,
                "platform": str(a.platform),
                "status": str(a.status),
                "label": a.label,
                "masked_phone": _mask_phone(a.phone_number),
                "created_at": _iso(created) if created else None,
                "after_r0": _after(created, R0_CUTOVER) if created else None,
                "after_r3_window": _after(created, R3_R10_WINDOW_START) if created else None,
                "looks_like_test": bool(
                    a.label
                    and any(
                        tok in (a.label or "").lower()
                        for tok in ("test", "p6-", "phase", "tmp", "dummy", "pytest", "r10")
                    )
                ),
            }
            acct_rows.append(row)
        out["tables"]["accounts"] = {
            "count": len(acct_rows),
            "rubika_ids": [r["id"] for r in acct_rows if "rubika" in r["platform"].lower()],
            "created_after_r0": [r for r in acct_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in acct_rows if r["after_r3_window"]],
            "looks_like_test": [r for r in acct_rows if r["looks_like_test"]],
            "all": acct_rows,
        }

        # ── campaigns ─────────────────────────────────────────────
        campaigns = db.query(Campaign).order_by(Campaign.id.asc()).all()
        camp_rows = []
        for c in campaigns:
            name = c.name or ""
            title = c.title or ""
            looks = any(
                tok in name.lower() or tok in title.lower()
                for tok in (
                    "p6-",
                    "test",
                    "phase",
                    "pytest",
                    "tmp",
                    "r10",
                    "dummy",
                    "hermetic",
                    "parity",
                )
            )
            camp_rows.append(
                {
                    "id": c.id,
                    "name": name,
                    "title": title,
                    "status": c.status,
                    "platform": str(c.platform),
                    "channel": c.channel,
                    "use_gpt": c.use_gpt,
                    "include_products": c.include_products,
                    "created_at": _iso(c.created_at),
                    "updated_at": _iso(c.updated_at),
                    "after_r0": _after(c.created_at, R0_CUTOVER),
                    "after_r3_window": _after(c.created_at, R3_R10_WINDOW_START),
                    "looks_like_test": looks,
                }
            )
        out["tables"]["campaigns"] = {
            "count": len(camp_rows),
            "created_after_r0": [r for r in camp_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in camp_rows if r["after_r3_window"]],
            "looks_like_test": [r for r in camp_rows if r["looks_like_test"]],
            "all": camp_rows,
        }

        # ── campaign_accounts ─────────────────────────────────────
        ca_rows = []
        for link in db.query(CampaignAccount).order_by(CampaignAccount.id.asc()).all():
            ca_rows.append(
                {
                    "id": link.id,
                    "campaign_id": link.campaign_id,
                    "account_id": link.account_id,
                    "priority": link.priority,
                    "enabled": link.enabled,
                    "created_at": _iso(link.created_at),
                    "after_r0": _after(link.created_at, R0_CUTOVER),
                    "after_r3_window": _after(link.created_at, R3_R10_WINDOW_START),
                }
            )
        out["tables"]["campaign_accounts"] = {
            "count": len(ca_rows),
            "created_after_r0": [r for r in ca_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in ca_rows if r["after_r3_window"]],
            "all": ca_rows,
        }

        # ── campaign_recipients ───────────────────────────────────
        cr_rows = []
        for r in db.query(CampaignRecipient).order_by(CampaignRecipient.id.asc()).all():
            cr_rows.append(
                {
                    "id": r.id,
                    "campaign_id": r.campaign_id,
                    "contact_id": r.contact_id,
                    "send_status": str(r.send_status),
                    "render_status": str(r.render_status),
                    "final_message_id": r.final_message_id,
                    "created_at": _iso(r.created_at),
                    "updated_at": _iso(r.updated_at),
                    "after_r0": _after(r.created_at, R0_CUTOVER),
                    "after_r3_window": _after(r.created_at, R3_R10_WINDOW_START),
                }
            )
        out["tables"]["campaign_recipients"] = {
            "count": len(cr_rows),
            "created_after_r0": [r for r in cr_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in cr_rows if r["after_r3_window"]],
            "all": cr_rows,
        }

        # ── messages ──────────────────────────────────────────────
        msg_rows = []
        for m in db.query(Message).order_by(Message.id.asc()).all():
            msg_rows.append(
                {
                    "id": m.id,
                    "campaign_id": m.campaign_id,
                    "account_id": m.account_id,
                    "contact_id": m.contact_id,
                    "dedupe_key": m.dedupe_key,
                    "created_at": _iso(m.created_at),
                    "updated_at": _iso(m.updated_at),
                    "after_r0": _after(m.created_at, R0_CUTOVER),
                    "after_r3_window": _after(m.created_at, R3_R10_WINDOW_START),
                }
            )
        out["tables"]["messages"] = {
            "count": len(msg_rows),
            "created_after_r0": [r for r in msg_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in msg_rows if r["after_r3_window"]],
            "all": msg_rows,
        }

        # ── staged_queue_items ────────────────────────────────────
        st_rows = []
        for s in db.query(StagedQueueItem).order_by(StagedQueueItem.id.asc()).all():
            st_rows.append(
                {
                    "id": s.id,
                    "campaign_id": s.campaign_id,
                    "contact_id": s.contact_id,
                    "status": s.status,
                    "skip_reason": s.skip_reason,
                    "created_at": _iso(s.created_at),
                    "updated_at": _iso(getattr(s, "updated_at", None)),
                    "after_r0": _after(s.created_at, R0_CUTOVER),
                    "after_r3_window": _after(s.created_at, R3_R10_WINDOW_START),
                }
            )
        out["tables"]["staged_queue_items"] = {
            "count": len(st_rows),
            "created_after_r0": [r for r in st_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in st_rows if r["after_r3_window"]],
            "by_status": {},
            "all": st_rows,
        }
        status_counts: dict[str, int] = {}
        for r in st_rows:
            status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        out["tables"]["staged_queue_items"]["by_status"] = status_counts

        # ── message_attempts ──────────────────────────────────────
        att_rows = []
        for a in db.query(MessageAttempt).order_by(MessageAttempt.id.asc()).all():
            att_rows.append(
                {
                    "id": a.id,
                    "message_id": a.message_id,
                    "attempt_no": a.attempt_no,
                    "status": str(a.status),
                    "platform_message_id": a.platform_message_id,
                    "error_code": a.error_code,
                    "created_at": _iso(a.created_at),
                    "after_r0": _after(a.created_at, R0_CUTOVER),
                    "after_r3_window": _after(a.created_at, R3_R10_WINDOW_START),
                }
            )
        out["tables"]["message_attempts"] = {
            "count": len(att_rows),
            "created_after_r0": [r for r in att_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in att_rows if r["after_r3_window"]],
            "with_platform_message_id": [
                r for r in att_rows if r["platform_message_id"]
            ],
            "all": att_rows,
        }

        # ── contacts ──────────────────────────────────────────────
        contact_rows = []
        for c in db.query(Contact).order_by(Contact.id.asc()).all():
            contact_rows.append(
                {
                    "id": c.id,
                    "campaign_id": c.campaign_id,
                    "alias": c.first_name or c.full_name,
                    "consent": c.consent_status,
                    "masked_phone": _mask_phone(c.phone_e164 or c.phone),
                    "created_at": _iso(c.created_at),
                    "updated_at": _iso(c.updated_at),
                    "after_r0": _after(c.created_at, R0_CUTOVER),
                    "after_r3_window": _after(c.created_at, R3_R10_WINDOW_START),
                }
            )
        out["tables"]["contacts"] = {
            "count": len(contact_rows),
            "created_after_r0": [r for r in contact_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in contact_rows if r["after_r3_window"]],
            "authorized_r10": [r for r in contact_rows if r["id"] in (2, 92)],
            "all_ids": [r["id"] for r in contact_rows],
        }

        # ── channel_sessions ──────────────────────────────────────
        sess_rows = []
        for s in db.query(ChannelSession).order_by(ChannelSession.id.asc()).all():
            sess_rows.append(
                {
                    "id": s.id,
                    "account_id": s.account_id,
                    "session_type": str(s.session_type),
                    "created_at": _iso(getattr(s, "created_at", None)),
                    "updated_at": _iso(getattr(s, "updated_at", None)),
                    "after_r0": _after(getattr(s, "created_at", None), R0_CUTOVER),
                    "after_r3_window": _after(
                        getattr(s, "created_at", None), R3_R10_WINDOW_START
                    ),
                    # Never dump ciphertext / secrets.
                    "has_ciphertext": bool(getattr(s, "ciphertext", None) or getattr(s, "session_blob", None) or getattr(s, "encrypted_payload", None)),
                }
            )
        out["tables"]["channel_sessions"] = {
            "count": len(sess_rows),
            "created_after_r0": [r for r in sess_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in sess_rows if r["after_r3_window"]],
            "for_accounts_12_79": [r for r in sess_rows if r["account_id"] in (12, 79)],
            "all": sess_rows,
        }

        # ── rubika_account_pool ───────────────────────────────────
        pool_rows = []
        for p in db.query(RubikaAccountPool).order_by(RubikaAccountPool.id.asc()).all():
            pool_rows.append(
                {
                    "id": p.id,
                    "account_id": p.account_id,
                    "phase": p.phase,
                    "priority": p.priority,
                    "created_at": _iso(getattr(p, "created_at", None)),
                    "after_r0": _after(getattr(p, "created_at", None), R0_CUTOVER),
                    "after_r3_window": _after(
                        getattr(p, "created_at", None), R3_R10_WINDOW_START
                    ),
                    "looks_like_test_phase": bool(
                        p.phase
                        and any(
                            tok in p.phase.lower()
                            for tok in ("p6-", "test", "phase", "tmp", "pytest")
                        )
                    ),
                }
            )
        out["tables"]["rubika_account_pool"] = {
            "count": len(pool_rows),
            "created_after_r0": [r for r in pool_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in pool_rows if r["after_r3_window"]],
            "test_looking_phases": [r for r in pool_rows if r["looks_like_test_phase"]],
            "all": pool_rows,
        }

        # ── rubika_sender_schedules ───────────────────────────────
        sched_rows = []
        for s in db.query(RubikaSenderSchedule).order_by(RubikaSenderSchedule.id.asc()).all():
            sched_rows.append(
                {
                    "id": s.id,
                    "phase": s.phase,
                    "start_hour": s.start_hour,
                    "end_hour": s.end_hour,
                    "max_per_hour": s.max_per_hour,
                    "is_active": s.is_active,
                    "created_at": _iso(getattr(s, "created_at", None)),
                    "updated_at": _iso(getattr(s, "updated_at", None)),
                    "after_r0": _after(getattr(s, "created_at", None), R0_CUTOVER),
                    "after_r3_window": _after(
                        getattr(s, "created_at", None), R3_R10_WINDOW_START
                    ),
                    "looks_like_test_phase": bool(
                        s.phase
                        and any(
                            tok in s.phase.lower()
                            for tok in ("p6-", "test", "phase", "tmp", "pytest")
                        )
                    ),
                }
            )
        out["tables"]["rubika_sender_schedules"] = {
            "count": len(sched_rows),
            "active": [r for r in sched_rows if r["is_active"]],
            "created_after_r0": [r for r in sched_rows if r["after_r0"]],
            "created_after_r3_window": [r for r in sched_rows if r["after_r3_window"]],
            "test_looking": [r for r in sched_rows if r["looks_like_test_phase"]],
            "all": sched_rows,
        }

        # ── max IDs / sequences ───────────────────────────────────
        max_ids = {}
        for table in (
            "accounts",
            "campaigns",
            "campaign_accounts",
            "campaign_recipients",
            "messages",
            "staged_queue_items",
            "message_attempts",
            "contacts",
            "channel_sessions",
            "rubika_account_pool",
            "rubika_sender_schedules",
        ):
            max_ids[table] = db.execute(text(f"SELECT COALESCE(MAX(id),0) FROM {table}")).scalar()
        out["max_ids"] = max_ids

        # ── running campaigns (send risk) ─────────────────────────
        running = [
            {"id": c.id, "name": c.name, "platform": str(c.platform), "status": c.status}
            for c in db.query(Campaign).filter(Campaign.status == "running").all()
        ]
        out["running_campaigns"] = running

        # heuristics: campaigns named p6-* etc
        out["suspicious_heuristics"]["p6_named_campaigns"] = [
            r for r in camp_rows if r["name"].startswith("p6-") or "p6-" in r["name"]
        ]
        out["suspicious_heuristics"]["test_phase_schedules"] = [
            r for r in sched_rows if r["looks_like_test_phase"]
        ]
        out["suspicious_heuristics"]["test_phase_pool_rows"] = [
            r for r in pool_rows if r["looks_like_test_phase"]
        ]

        # New Rubika accounts after R0
        new_rubika_accts = [
            r
            for r in acct_rows
            if "rubika" in r["platform"].lower() and r["after_r0"]
        ]
        out["summary"] = {
            "total_accounts": len(acct_rows),
            "total_rubika_accounts": len(
                [r for r in acct_rows if "rubika" in r["platform"].lower()]
            ),
            "rubika_accounts_created_after_r0": new_rubika_accts,
            "campaigns_created_after_r0_count": len(
                [r for r in camp_rows if r["after_r0"]]
            ),
            "campaigns_created_after_r3_window_count": len(
                [r for r in camp_rows if r["after_r3_window"]]
            ),
            "contacts_created_after_r0_count": len(
                [r for r in contact_rows if r["after_r0"]]
            ),
            "contacts_created_after_r3_window_count": len(
                [r for r in contact_rows if r["after_r3_window"]]
            ),
            "messages_created_after_r0_count": len(
                [r for r in msg_rows if r["after_r0"]]
            ),
            "messages_created_after_r3_window_count": len(
                [r for r in msg_rows if r["after_r3_window"]]
            ),
            "message_attempts_with_platform_id": len(
                [r for r in att_rows if r["platform_message_id"]]
            ),
            "r10_dedicated_campaign_exists": any(
                "r10" in (r["name"] or "").lower() for r in camp_rows
            ),
            "running_campaign_count": len(running),
        }

        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"WROTE {OUT_JSON}")
        print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
        print("SUSPICIOUS_P6_CAMPAIGNS", len(out["suspicious_heuristics"]["p6_named_campaigns"]))
        print("TEST_SCHEDULES", len(out["suspicious_heuristics"]["test_phase_schedules"]))
        print("TEST_POOL", len(out["suspicious_heuristics"]["test_phase_pool_rows"]))
        print("NEW_RUBIKA_AFTER_R0", len(new_rubika_accts))
        print("RUNNING", running)
    finally:
        db.close()


if __name__ == "__main__":
    main()
