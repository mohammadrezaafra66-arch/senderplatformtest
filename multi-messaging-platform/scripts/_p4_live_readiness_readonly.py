"""LIVE READ-ONLY production readiness check. No writes, no sends, no workers restart."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Refuse if not pointed at production app DB for this check.
DATABASE_URL = os.environ["DATABASE_URL"]
if "/mmp_db" not in DATABASE_URL.split("?")[0]:
    raise SystemExit("REFUSE: live check requires DATABASE_URL database mmp_db")


def main() -> int:
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    out: dict = {}

    # --- accounts 12 / 79 ---
    from core_engine.models import Account, AccountStatus, PlatformType, RubikaAccountPool
    from core_engine.services.account_session_wiring import (
        evaluate_account_session_readiness,
    )
    from workers.rubika_account_pool import resolve_current_phase

    accounts = {}
    for aid in (12, 79):
        acc = db.query(Account).filter(Account.id == aid).one_or_none()
        if acc is None:
            accounts[aid] = {"exists": False}
            continue
        readiness = evaluate_account_session_readiness(
            db, acc, rubika_delivery_mode="user_account"
        )
        phase = resolve_current_phase(db)
        pool = (
            db.query(RubikaAccountPool)
            .filter(
                RubikaAccountPool.account_id == aid,
                RubikaAccountPool.phase == phase,
            )
            .first()
        )
        # Schedule applicability: active RubikaSenderSchedule for current phase
        from core_engine.models import RubikaSenderSchedule

        sched = (
            db.query(RubikaSenderSchedule)
            .filter(
                RubikaSenderSchedule.is_active.is_(True),
                RubikaSenderSchedule.phase == phase,
            )
            .first()
        )
        status_val = acc.status.value if hasattr(acc.status, "value") else str(acc.status)
        accounts[aid] = {
            "exists": True,
            "status": status_val,
            "platform": acc.platform.value if hasattr(acc.platform, "value") else str(acc.platform),
            "account_enabled": status_val.upper() == "ACTIVE",
            "session_readiness_code": readiness.code,
            "session_ready": bool(getattr(readiness, "ready", readiness.code == "READY")),
            "pool_phase": phase,
            "in_current_pool": pool is not None,
            "active_schedule_for_phase": sched is not None,
            "schedule_phase": getattr(sched, "phase", None),
        }
        # READY composite for report
        accounts[aid]["READY"] = bool(
            accounts[aid]["account_enabled"]
            and accounts[aid]["session_ready"]
            and accounts[aid]["in_current_pool"]
            and accounts[aid]["active_schedule_for_phase"]
        )
    out["accounts"] = accounts

    # --- campaigns ---
    camps = db.execute(
        text(
            "SELECT id, name, status FROM campaigns WHERE id IN (101,102) ORDER BY id"
        )
    ).mappings().all()
    out["campaigns_101_102"] = [dict(r) for r in camps]

    running = db.execute(
        text(
            "SELECT id, name, status FROM campaigns WHERE lower(status)='running' ORDER BY id"
        )
    ).mappings().all()
    out["running_campaigns"] = [dict(r) for r in running]
    rem_running = [
        r
        for r in running
        if any(
            tok in (r["name"] or "").upper()
            for tok in ("R10", "REMEDIAT", "CONTROLLED", "PYTEST", "P6-", "PROD-GUARD")
        )
    ]
    out["running_remediation_campaigns"] = [dict(r) for r in rem_running]
    out["RUNNING_REMEDIATION_CAMPAIGN_COUNT"] = len(rem_running)

    # --- integrity counts ---
    counts = {}
    counts["production_accounts"] = db.execute(text("SELECT count(*) FROM accounts")).scalar()
    counts["rubika_accounts"] = db.execute(
        text("SELECT count(*) FROM accounts WHERE platform::text ILIKE '%rubika%'")
    ).scalar()
    counts["campaigns"] = db.execute(text("SELECT count(*) FROM campaigns")).scalar()
    counts["contacts"] = db.execute(text("SELECT count(*) FROM contacts")).scalar()
    counts["messages"] = db.execute(text("SELECT count(*) FROM messages")).scalar()
    out["counts"] = {k: int(v or 0) for k, v in counts.items()}

    # remediation debris heuristic: campaigns named like remediation still present beyond 101/102
    debris_camps = db.execute(
        text(
            """
            SELECT id, name, status FROM campaigns
            WHERE id NOT IN (101, 102)
              AND (
                name ILIKE '%remediat%'
                OR name ILIKE 'p6-%'
                OR name ILIKE 'prod-guard-%'
                OR name ILIKE '%pytest%'
                OR name ILIKE 'r10-%'
              )
            ORDER BY id
            """
        )
    ).mappings().all()
    # staged residue for 101/102 that is not terminal
    staged = db.execute(
        text(
            """
            SELECT campaign_id, status, count(*) AS n
            FROM staged_queue_items
            WHERE campaign_id IN (101, 102)
              AND status NOT IN ('queued','skipped','blocked')
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        )
    ).mappings().all()
    # "queued" after R10 is historical staging state — note separately
    staged_all = db.execute(
        text(
            """
            SELECT campaign_id, status, count(*) AS n
            FROM staged_queue_items
            WHERE campaign_id IN (101, 102)
            GROUP BY 1, 2 ORDER BY 1, 2
            """
        )
    ).mappings().all()
    out["remediation_named_campaigns_excluding_101_102"] = [dict(r) for r in debris_camps]
    out["staged_101_102_all"] = [dict(r) for r in staged_all]
    out["LIVE_REMEDIATION_DEBRIS_COUNT"] = len(debris_camps)

    db.close()

    # --- redis coverage + queues (read-only) ---
    async def redis_ro():
        from core_engine.services.redis_client import get_redis_client, reset_redis_client
        from workers.redis_keys import queue_key, worker_account_coverage_key

        reset_redis_client()
        r = get_redis_client()
        result = {}
        for aid in (12, 79):
            ckey = worker_account_coverage_key("rubika", aid)
            qkey = queue_key("rubika", aid)
            exists = bool(await r.exists(ckey))
            ttl = await r.ttl(ckey)
            llen = int(await r.llen(qkey))
            result[aid] = {
                "coverage": exists,
                "coverage_ttl_seconds": int(ttl) if ttl is not None else None,
                "coverage_ttl_healthy": bool(exists and ttl is not None and ttl > 0),
                "queue_len": llen,
            }
        # scan for unexpected remediation queue keys (read-only KEYS is heavy — use known pattern via SCAN)
        unexpected = []
        async for key in r.scan_iter(match="queue:rubika:*", count=100):
            k = key if isinstance(key, str) else key.decode()
            # allow only 12 and 79 non-empty? report any with llen>0
            n = int(await r.llen(k))
            if n > 0:
                unexpected.append({"key": k, "llen": n})
        result["nonempty_rubika_queues"] = unexpected
        return result

    out["redis"] = asyncio.run(redis_ro())

    # --- config (from running process settings / env) ---
    from core_engine.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    out["controlled_production"] = {
        "CONTROLLED_PRODUCTION_ENABLED": bool(
            getattr(s, "CONTROLLED_PRODUCTION_ENABLED", False)
        ),
        "DEFAULT_MAX_TOTAL_MESSAGES": int(
            getattr(s, "CONTROLLED_PRODUCTION_DEFAULT_MAX_TOTAL_MESSAGES", 5) or 5
        ),
    }

    # --- code/runtime guard presence (import-level evidence, no live rows) ---
    from core_engine.services import campaign_preflight as pf
    from core_engine.services import campaign_production_guards as g
    from core_engine.services import queue_bridge as qb
    from core_engine.services import phase4_prepare as prep
    import inspect

    src_pf = inspect.getsource(pf.evaluate_campaign_send_preflight)
    src_qb = inspect.getsource(qb.push_staged_items_to_worker_queue)
    src_prep = inspect.getsource(prep.prepare_campaign_messages)
    out["guards_active"] = {
        "RECIPIENT_VALIDATION_ACTIVE": (
            "INVALID_RECIPIENT_PHONE" in src_pf
            and "validate_rubika_contact_phone" in src_prep
            and hasattr(g, "is_valid_iran_mobile_e164")
        ),
        "PLACEHOLDER_GUARD_ACTIVE": (
            "UNRESOLVED_TEMPLATE_PLACEHOLDER" in src_pf
            and hasattr(g, "find_unresolved_placeholders")
        ),
        "WORKER_COVERAGE_GATE_ACTIVE": "NO_WORKER_CONSUMER" in src_pf,
        "ASSIGNMENT_GUARD_ACTIVE": (
            "INVALID_MESSAGE_ASSIGNMENT" in src_pf
            and hasattr(g, "scan_assignment_consistency")
        ),
        "SEND_LIMIT_GUARD_ACTIVE": (
            "CAMPAIGN_SEND_LIMIT_REACHED" in src_pf
            and hasattr(g, "evaluate_send_limit")
            and "would_exceed_send_limit" in src_qb
        ),
        "DUPLICATE_PROTECTION_ACTIVE": (
            "MESSAGE_ALREADY_SENT" in src_qb
            and hasattr(g, "message_has_terminal_success")
        ),
        "CAMPAIGN_STATE_GUARD_ACTIVE": (
            "CampaignStatus.RUNNING" in src_qb
            or 'Campaign.status == CampaignStatus.RUNNING.value' in src_qb
        ),
        "OBSERVABILITY_ACTIVE": (
            "valid_recipient_count" in src_pf
            and "worker_coverage_accounts" in src_pf
            and "block_code" in src_pf
        ),
    }

    # never print secrets
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
