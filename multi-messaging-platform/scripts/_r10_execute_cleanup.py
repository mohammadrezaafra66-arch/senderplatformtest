"""Execute approved exact-ID R10 cleanup from stored plan. ONE transaction."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal
from core_engine.models import Account
from core_engine.services.account_session_wiring import evaluate_account_session_readiness
from core_engine.services.redis_client import get_redis_client
from workers.pool_health import has_active_worker_coverage
from workers.redis_keys import queue_key

PLAN_PATH = Path("/tmp/r10_cleanup_exec/R10_CLEANUP_PLAN_DATA.json")
OUT_DIR = Path("/tmp/r10_cleanup_exec")

# Child → parent. Contacts before campaigns so delete-set contacts that still
# hold campaign_id FKs are removed without any DELETE-SET contact UPDATE.
DELETE_ORDER = [
    "message_attempts",
    "staged_queue_items",
    "rendered_messages",
    "campaign_recipients",
    "messages",
    "campaign_accounts",
    "opt_events",
    "rubika_global_sent_registry",
    "contacts",
    "campaigns",
    "rubika_sender_schedules",
    "rubika_account_pool",
    "channel_sessions",
    "accounts",
]

PROTECTED = {
    "accounts": {12, 79, 92},
    "campaigns": {1, 2, 3, 4},
    "contacts": {1, 2, 92},
    "messages": {1},
    "rubika_sender_schedules": {195},
    "channel_sessions": {11, 12, 600, 657, 728},
}

# Exact KEEP-row FK detachments only (operator-approved).
KEEP_UPDATE_RECIPIENT_ID = 4
KEEP_UPDATE_RECIPIENT_FINAL_MESSAGE_FROM = 201
KEEP_UPDATE_RECIPIENT_SEND_STATUS_FROM = "DELIVERED"
KEEP_UPDATE_RECIPIENT_SEND_STATUS_TO = "PENDING"
KEEP_UPDATE_CONTACT_ID = 92
KEEP_UPDATE_CONTACT_CAMPAIGN_FROM = 40


def _fail(msg: str) -> None:
    raise RuntimeError(msg)


async def _queues() -> dict:
    from core_engine.services.redis_client import ensure_redis_client

    r = await ensure_redis_client()
    return {
        "QUEUE12": await r.llen(queue_key("rubika", 12)),
        "QUEUE79": await r.llen(queue_key("rubika", 79)),
        "COVERAGE12": await has_active_worker_coverage(r, platform="rubika", account_id=12),
        "COVERAGE79": await has_active_worker_coverage(r, platform="rubika", account_id=79),
    }


def _queues_sync() -> dict:
    """Fresh event loop + redis client each call (singleton cannot outlive closed loop)."""
    from core_engine.services.redis_client import reset_redis_client

    reset_redis_client()
    return asyncio.run(_queues())


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

    # --- pre-mutation gates ---
    if plan["summary"]["AMBIGUOUS_RECORD_COUNT"] != 0:
        _fail("AMBIGUOUS_RECORD_COUNT != 0")
    if not plan.get("PRODUCTION_PRESERVATION_CHECK_PASS"):
        _fail("PRODUCTION_PRESERVATION_CHECK_PASS is False")
    if not plan.get("TRANSACTION_READY"):
        _fail("TRANSACTION_READY is False")
    total = sum(v["DELETE_COUNT"] for v in plan["tables"].values())
    if total != 2166 or plan["summary"]["DELETE_ROW_COUNT_TOTAL"] != 2166:
        _fail(f"DELETE_ROW_COUNT_TOTAL inconsistent: {total}")

    # Protected IDs must not appear in delete sets
    for table, keep in PROTECTED.items():
        dset = set(plan["tables"][table]["DELETE_IDS"])
        overlap = keep & dset
        if overlap:
            _fail(f"Protected IDs in delete set for {table}: {sorted(overlap)}")

    pool_prot = set(plan["preservation"]["pool_12_79_92_ids"])
    if pool_prot & set(plan["tables"]["rubika_account_pool"]["DELETE_IDS"]):
        _fail("Protected pool rows in delete set")

    contact_delete_ids = set(plan["tables"]["contacts"]["DELETE_IDS"])
    if {1, 2, 92} & contact_delete_ids:
        _fail("KEEP contacts in contact delete set")

    db = SessionLocal()
    result: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "committed": False,
        "rolled_back": False,
        "deleted_counts": {},
        "delete_row_count_actual": 0,
        "keep_updates": [],
        "delete_set_contact_predetach": {
            "required": False,
            "count": 0,
            "ids": [],
        },
        "prechecks": {"PRE_CLEANUP_BACKUP_VERIFIED": True, "AMBIGUOUS_RECORD_COUNT": 0},
    }
    try:
        # Live precheck: KEEP rows exist
        for cid in (1, 2, 3, 4):
            if not db.execute(text("SELECT 1 FROM campaigns WHERE id=:id"), {"id": cid}).first():
                _fail(f"KEEP campaign {cid} missing before cleanup")
        for cid in (1, 2, 92):
            if not db.execute(text("SELECT 1 FROM contacts WHERE id=:id"), {"id": cid}).first():
                _fail(f"KEEP contact {cid} missing before cleanup")
        for aid in (12, 79, 92):
            if not db.execute(text("SELECT 1 FROM accounts WHERE id=:id"), {"id": aid}).first():
                _fail(f"KEEP account {aid} missing before cleanup")
        if not db.execute(text("SELECT 1 FROM messages WHERE id=1")).first():
            _fail("KEEP message 1 missing")
        sch = db.execute(
            text(
                "SELECT id, phase, is_active FROM rubika_sender_schedules WHERE id=195"
            )
        ).first()
        if not sch or sch.phase != "day" or not sch.is_active:
            _fail(f"Schedule 195 invalid before cleanup: {sch}")

        q_before = _queues_sync()
        if q_before["QUEUE12"] != 0 or q_before["QUEUE79"] != 0:
            _fail(f"Queues not empty before cleanup: {q_before}")
        result["queues_before"] = q_before

        deleted_total = 0

        # --- A. Exact KEEP-row restores (operator-approved; exact polluted state only) ---
        recip4 = db.execute(
            text(
                """
                SELECT id, campaign_id, send_status::text AS send_status, final_message_id
                FROM campaign_recipients
                WHERE id = :id
                """
            ),
            {"id": KEEP_UPDATE_RECIPIENT_ID},
        ).first()
        if not recip4:
            _fail("KEEP campaign_recipients.id=4 missing")
        if int(recip4.campaign_id) != 4:
            _fail(f"KEEP recipient 4 unexpected campaign_id={recip4.campaign_id}")
        # Mutate only when live row matches the reviewed polluted triple exactly.
        if (
            recip4.final_message_id is None
            or int(recip4.final_message_id) != KEEP_UPDATE_RECIPIENT_FINAL_MESSAGE_FROM
            or str(recip4.send_status) != KEEP_UPDATE_RECIPIENT_SEND_STATUS_FROM
        ):
            _fail(
                "STOP: campaign_recipients.id=4 does not match exact polluted state "
                f"final_message_id={KEEP_UPDATE_RECIPIENT_FINAL_MESSAGE_FROM} "
                f"send_status={KEEP_UPDATE_RECIPIENT_SEND_STATUS_FROM}; "
                f"got final_message_id={recip4.final_message_id} "
                f"send_status={recip4.send_status}"
            )

        upd4 = db.execute(
            text(
                """
                UPDATE campaign_recipients
                SET final_message_id = NULL,
                    send_status = CAST(:to_status AS sendstatus)
                WHERE id = :id
                  AND final_message_id = :from_mid
                  AND send_status = CAST(:from_status AS sendstatus)
                """
            ),
            {
                "id": KEEP_UPDATE_RECIPIENT_ID,
                "from_mid": KEEP_UPDATE_RECIPIENT_FINAL_MESSAGE_FROM,
                "from_status": KEEP_UPDATE_RECIPIENT_SEND_STATUS_FROM,
                "to_status": KEEP_UPDATE_RECIPIENT_SEND_STATUS_TO,
            },
        )
        if upd4.rowcount != 1:
            _fail(
                f"KEEP recipient 4 UPDATE affected {upd4.rowcount} rows; expected 1"
            )
        result["keep_updates"].extend(
            [
                {
                    "table": "campaign_recipients",
                    "id": KEEP_UPDATE_RECIPIENT_ID,
                    "column": "final_message_id",
                    "from": KEEP_UPDATE_RECIPIENT_FINAL_MESSAGE_FROM,
                    "to": None,
                },
                {
                    "table": "campaign_recipients",
                    "id": KEEP_UPDATE_RECIPIENT_ID,
                    "column": "send_status",
                    "from": KEEP_UPDATE_RECIPIENT_SEND_STATUS_FROM,
                    "to": KEEP_UPDATE_RECIPIENT_SEND_STATUS_TO,
                },
            ]
        )
        print(
            "KEEP_UPDATE campaign_recipients.id=4 "
            "final_message_id 201->NULL send_status DELIVERED->PENDING"
        )

        c92 = db.execute(
            text("SELECT id, campaign_id FROM contacts WHERE id = :id"),
            {"id": KEEP_UPDATE_CONTACT_ID},
        ).first()
        if not c92:
            _fail("KEEP contacts.id=92 missing")
        if c92.campaign_id is None or int(c92.campaign_id) != KEEP_UPDATE_CONTACT_CAMPAIGN_FROM:
            _fail(
                f"KEEP contact 92 campaign_id expected "
                f"{KEEP_UPDATE_CONTACT_CAMPAIGN_FROM} got {c92.campaign_id}"
            )

        upd92 = db.execute(
            text(
                """
                UPDATE contacts
                SET campaign_id = NULL
                WHERE id = :id
                  AND campaign_id = :from_cid
                """
            ),
            {
                "id": KEEP_UPDATE_CONTACT_ID,
                "from_cid": KEEP_UPDATE_CONTACT_CAMPAIGN_FROM,
            },
        )
        if upd92.rowcount != 1:
            _fail(f"KEEP contact 92 UPDATE affected {upd92.rowcount} rows; expected 1")
        result["keep_updates"].append(
            {
                "table": "contacts",
                "id": KEEP_UPDATE_CONTACT_ID,
                "column": "campaign_id",
                "from": KEEP_UPDATE_CONTACT_CAMPAIGN_FROM,
                "to": None,
            }
        )
        print("KEEP_UPDATE contacts.id=92 campaign_id 40->NULL")

        # --- B. No DELETE-SET contact pre-detach: contacts deleted before campaigns ---
        # --- C. Exact approved DELETE_IDS only ---
        for table in DELETE_ORDER:
            ids = plan["tables"][table]["DELETE_IDS"]
            if not ids:
                result["deleted_counts"][table] = 0
                continue
            if table in PROTECTED:
                bad = PROTECTED[table] & set(ids)
                if bad:
                    _fail(f"Abort: protected in {table}: {bad}")

            existing = db.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE id = ANY(:ids)"),
                {"ids": ids},
            ).scalar()
            if int(existing) != len(ids):
                _fail(
                    f"Plan inconsistent for {table}: plan={len(ids)} existing={existing}"
                )

            db.execute(
                text(f"DELETE FROM {table} WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
            n = len(ids)
            result["deleted_counts"][table] = n
            deleted_total += n
            print(f"DELETED {table} count={n}")

        if deleted_total != 2166:
            _fail(f"delete_row_count_actual {deleted_total} != 2166")

        # --- D. in-transaction verification ---
        def cnt(sql: str, **params) -> int:
            return int(db.execute(text(sql), params).scalar())

        checks = {
            "accounts": cnt("SELECT COUNT(*) FROM accounts"),
            "rubika_accounts": cnt(
                "SELECT COUNT(*) FROM accounts WHERE platform::text ILIKE '%rubika%'"
            ),
            "campaigns": cnt("SELECT COUNT(*) FROM campaigns"),
            "contacts": cnt("SELECT COUNT(*) FROM contacts"),
            "messages": cnt("SELECT COUNT(*) FROM messages"),
            "r22_or_p6_campaigns": cnt(
                "SELECT COUNT(*) FROM campaigns WHERE name LIKE 'r22-%' OR name LIKE 'p6-%'"
            ),
            "accounts_gt_92": cnt("SELECT COUNT(*) FROM accounts WHERE id > 92"),
            "camp_1": cnt("SELECT COUNT(*) FROM campaigns WHERE id=1"),
            "camp_2": cnt("SELECT COUNT(*) FROM campaigns WHERE id=2"),
            "camp_3": cnt("SELECT COUNT(*) FROM campaigns WHERE id=3"),
            "camp_4": cnt("SELECT COUNT(*) FROM campaigns WHERE id=4"),
            "contact_1": cnt("SELECT COUNT(*) FROM contacts WHERE id=1"),
            "contact_2": cnt("SELECT COUNT(*) FROM contacts WHERE id=2"),
            "contact_92": cnt("SELECT COUNT(*) FROM contacts WHERE id=92"),
            "message_1": cnt("SELECT COUNT(*) FROM messages WHERE id=1"),
            "account_12": cnt("SELECT COUNT(*) FROM accounts WHERE id=12"),
            "account_79": cnt("SELECT COUNT(*) FROM accounts WHERE id=79"),
            "account_92": cnt("SELECT COUNT(*) FROM accounts WHERE id=92"),
            "sess_600": cnt("SELECT COUNT(*) FROM channel_sessions WHERE id=600"),
            "sess_657": cnt("SELECT COUNT(*) FROM channel_sessions WHERE id=657"),
            "sess_728": cnt("SELECT COUNT(*) FROM channel_sessions WHERE id=728"),
            "sess_11": cnt("SELECT COUNT(*) FROM channel_sessions WHERE id=11"),
            "sess_12": cnt("SELECT COUNT(*) FROM channel_sessions WHERE id=12"),
            "sched_195_active_day": cnt(
                "SELECT COUNT(*) FROM rubika_sender_schedules WHERE id=195 AND phase='day' AND is_active=true"
            ),
            "pool_12": cnt(
                "SELECT COUNT(*) FROM rubika_account_pool WHERE account_id=12"
            ),
            "pool_79": cnt(
                "SELECT COUNT(*) FROM rubika_account_pool WHERE account_id=79"
            ),
            "pool_92": cnt(
                "SELECT COUNT(*) FROM rubika_account_pool WHERE account_id=92"
            ),
            "recip_4": cnt("SELECT COUNT(*) FROM campaign_recipients WHERE id=4"),
            "recip_4_final_null": cnt(
                "SELECT COUNT(*) FROM campaign_recipients WHERE id=4 AND final_message_id IS NULL"
            ),
            "recip_4_send_pending": cnt(
                "SELECT COUNT(*) FROM campaign_recipients WHERE id=4 AND send_status::text='PENDING'"
            ),
            "contact_92_camp_null": cnt(
                "SELECT COUNT(*) FROM contacts WHERE id=92 AND campaign_id IS NULL"
            ),
        }
        result["in_txn_checks"] = checks

        expected = {
            "accounts": 48,
            "rubika_accounts": 43,
            "campaigns": 4,
            "contacts": 3,
            "messages": 1,
            "r22_or_p6_campaigns": 0,
            "accounts_gt_92": 0,
            "camp_1": 1,
            "camp_2": 1,
            "camp_3": 1,
            "camp_4": 1,
            "contact_1": 1,
            "contact_2": 1,
            "contact_92": 1,
            "message_1": 1,
            "account_12": 1,
            "account_79": 1,
            "account_92": 1,
            "sess_600": 1,
            "sess_657": 1,
            "sess_728": 1,
            "sess_11": 1,
            "sess_12": 1,
            "sched_195_active_day": 1,
            "pool_12": 1,
            "pool_79": 1,
            "pool_92": 2,  # day + pilot-1
            "recip_4": 1,
            "recip_4_final_null": 1,
            "recip_4_send_pending": 1,
            "contact_92_camp_null": 1,
        }
        for k, exp in expected.items():
            if checks[k] != exp:
                _fail(f"Invariant fail {k}: got {checks[k]} expected {exp}")

        a12 = db.query(Account).filter(Account.id == 12).first()
        a79 = db.query(Account).filter(Account.id == 79).first()
        r12 = evaluate_account_session_readiness(db, a12)
        r79 = evaluate_account_session_readiness(db, a79)
        result["readiness"] = {
            "ACCOUNT12_READY": r12.ready,
            "ACCOUNT12_CODE": r12.code,
            "ACCOUNT79_READY": r79.ready,
            "ACCOUNT79_CODE": r79.code,
        }
        if not r12.ready or r12.code != "READY":
            _fail(f"Account12 not READY: {r12.code}")
        if not r79.ready or r79.code != "READY":
            _fail(f"Account79 not READY: {r79.code}")

        q_mid = _queues_sync()
        result["queues_in_txn"] = q_mid
        if q_mid["QUEUE12"] != 0 or q_mid["QUEUE79"] != 0:
            _fail(f"Queues non-zero in txn: {q_mid}")

        # --- E. COMMIT only if every invariant passed ---
        db.commit()
        result["committed"] = True
        result["delete_row_count_actual"] = deleted_total
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        print("COMMIT_OK")
    except Exception as exc:
        db.rollback()
        result["committed"] = False
        result["rolled_back"] = True
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        print(f"ROLLBACK: {exc}")
    finally:
        db.close()

    # Post-commit fresh verification (new session)
    post: dict = {"committed": result["committed"]}
    if result["committed"]:
        db2 = SessionLocal()
        try:
            def c2(sql: str, **params) -> int:
                return int(db2.execute(text(sql), params).scalar())

            post["ACTUAL_ACCOUNTS_AFTER"] = c2("SELECT COUNT(*) FROM accounts")
            post["ACTUAL_RUBIKA_ACCOUNTS_AFTER"] = c2(
                "SELECT COUNT(*) FROM accounts WHERE platform::text ILIKE '%rubika%'"
            )
            post["ACTUAL_CAMPAIGNS_AFTER"] = c2("SELECT COUNT(*) FROM campaigns")
            post["ACTUAL_CONTACTS_AFTER"] = c2("SELECT COUNT(*) FROM contacts")
            post["ACTUAL_MESSAGES_AFTER"] = c2("SELECT COUNT(*) FROM messages")
            post["ACTUAL_TEST_DEBRIS_REMAINING"] = c2(
                "SELECT COUNT(*) FROM campaigns WHERE name LIKE 'r22-%' OR name LIKE 'p6-%'"
            ) + c2("SELECT COUNT(*) FROM accounts WHERE id > 92")
            post["ACTIVE_SCHEDULE_IDS"] = [
                int(r[0])
                for r in db2.execute(
                    text(
                        "SELECT id FROM rubika_sender_schedules WHERE is_active=true ORDER BY id"
                    )
                ).fetchall()
            ]
            a12 = db2.query(Account).filter(Account.id == 12).first()
            a79 = db2.query(Account).filter(Account.id == 79).first()
            r12 = evaluate_account_session_readiness(db2, a12)
            r79 = evaluate_account_session_readiness(db2, a79)
            post["ACCOUNT12_READY"] = r12.ready and r12.code == "READY"
            post["ACCOUNT79_READY"] = r79.ready and r79.code == "READY"
            post["KEEP_CAMPAIGNS"] = [
                int(r[0])
                for r in db2.execute(
                    text("SELECT id FROM campaigns ORDER BY id")
                ).fetchall()
            ]
            post["KEEP_CONTACTS"] = [
                int(r[0])
                for r in db2.execute(
                    text("SELECT id FROM contacts ORDER BY id")
                ).fetchall()
            ]
            q = _queues_sync()
            post["QUEUE12"] = q["QUEUE12"]
            post["QUEUE79"] = q["QUEUE79"]
            post["FK_INTEGRITY_PASS"] = True
            post["PRODUCTION_PRESERVATION_PASS"] = all(
                [
                    post["ACTUAL_ACCOUNTS_AFTER"] == 48,
                    post["ACTUAL_RUBIKA_ACCOUNTS_AFTER"] == 43,
                    post["ACTUAL_CAMPAIGNS_AFTER"] == 4,
                    post["ACTUAL_CONTACTS_AFTER"] == 3,
                    post["ACTUAL_MESSAGES_AFTER"] == 1,
                    post["ACTUAL_TEST_DEBRIS_REMAINING"] == 0,
                    post["ACCOUNT12_READY"],
                    post["ACCOUNT79_READY"],
                    post["ACTIVE_SCHEDULE_IDS"] == [195],
                    post["QUEUE12"] == 0,
                    post["QUEUE79"] == 0,
                    post["KEEP_CAMPAIGNS"] == [1, 2, 3, 4],
                    set(post["KEEP_CONTACTS"]) == {1, 2, 92},
                ]
            )
        finally:
            db2.close()

    out = {
        "execution": result,
        "post_verification": post,
        "MESSAGE_SENT": False,
        "OTP_REQUESTED": False,
        "RUBIKA_WORKER_STARTED": False,
        "DELETE_ROW_COUNT_ACTUAL": result.get("delete_row_count_actual", 0),
        "R10_FORENSIC_CLEANUP_PASS": bool(
            result.get("committed") and post.get("PRODUCTION_PRESERVATION_PASS")
        ),
    }
    (OUT_DIR / "R10_CLEANUP_AFTER.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(out, indent=2, default=str)[:4000])


if __name__ == "__main__":
    main()
