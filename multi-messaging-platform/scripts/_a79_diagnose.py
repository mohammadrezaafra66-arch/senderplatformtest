#!/usr/bin/env python3
"""Read-only Account79 worker/session/runtime diagnosis."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

ACCOUNT_ID = 79


async def main() -> None:
    import redis.asyncio as aioredis
    from sqlalchemy import func

    from core_engine.database import SessionLocal
    from core_engine.models import Account, ChannelSession, Message, StagedQueueItem
    from core_engine.services.account_runtime_status import compute_account_runtime_status
    from core_engine.services.campaign_sender_eligibility import evaluate_campaign_sender_eligibility
    from core_engine.services.rubika_canonical_runtime import select_legacy_rubika_session_row
    from workers.redis_keys import worker_account_coverage_key
    from workers.config import get_worker_settings
    from workers.account_pool import parse_account_id_list
    from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings

    r = aioredis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    db = SessionLocal()
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "ACCOUNT_ID": ACCOUNT_ID}
    try:
        settings = get_worker_settings()
        actual_ids, meta = resolve_rubika_worker_account_ids_from_settings(settings, db=db)
        out["ACCOUNT79_PIN_CONFIGURED"] = ACCOUNT_ID in meta.get("pinned_ids", [])
        out["RUBIKA_ACCOUNT_IDS_EFFECTIVE"] = meta.get("pinned_ids")
        out["ACTUAL_WORKER_IDS"] = actual_ids
        out["DISCOVERY_META"] = meta

        cov_key = worker_account_coverage_key("rubika", ACCOUNT_ID)
        cov_val = await r.get(cov_key)
        cov_ttl = await r.ttl(cov_key)
        out["ACCOUNT79_WORKER_COVERAGE_PRESENT"] = bool(cov_val)
        out["coverage_key"] = cov_key
        out["coverage_ttl"] = int(cov_ttl)
        if cov_val:
            out["coverage_payload"] = json.loads(cov_val)

        keys = []
        async for k in r.scan_iter("worker:coverage:rubika:*"):
            keys.append(k.decode() if isinstance(k, bytes) else k)
        out["ALL_COVERAGE_KEYS"] = sorted(keys)

        hb_keys = []
        async for k in r.scan_iter("worker:alive:rubika:*"):
            hb_keys.append(k.decode() if isinstance(k, bytes) else k)
        hbs = {}
        for hk in hb_keys:
            v = await r.get(hk)
            if v:
                hbs[hk] = json.loads(v)
        out["HEARTBEAT_KEYS"] = sorted(hb_keys)
        out["HEARTBEATS"] = hbs
        out["ACCOUNT79_WORKER_HEARTBEAT_PRESENT"] = any(
            ACCOUNT_ID in (hb.get("assigned_account_ids") or [])
            for hb in hbs.values()
        )
        out["ACCOUNT79_WORKER_PROCESS_PRESENT"] = out["ACCOUNT79_WORKER_HEARTBEAT_PRESENT"]

        acct = db.query(Account).filter(Account.id == ACCOUNT_ID).one()
        runtime = compute_account_runtime_status(db, acct)
        elig = evaluate_campaign_sender_eligibility(db, acct, runtime=runtime)
        out["ACCOUNT79_RUNTIME_STATUS"] = runtime.runtime_status
        out["ACCOUNT79_WORKER_READY"] = elig.worker_ready
        out["ACCOUNT79_DISPATCH_READY"] = elig.dispatch_ready
        out["ACCOUNT79_CAMPAIGN_ELIGIBLE"] = elig.campaign_eligible
        out["runtime_reason"] = runtime.reason_code
        out["worker_covered"] = runtime.worker_covered
        out["worker_state"] = runtime.worker_state
        out["runtime_details"] = runtime.details

        rows = db.query(ChannelSession).filter(ChannelSession.account_id == ACCOUNT_ID).all()
        out["SESSION_IDS"] = [(int(s.id), str(s.session_status)) for s in rows]
        out["SESSION_COUNT"] = len(rows)

        legacy = select_legacy_rubika_session_row(db, ACCOUNT_ID)
        out["LEGACY_SESSION_SELECTED"] = int(legacy.id) if legacy else None
        out["LEGACY_SESSION_STATUS"] = str(legacy.session_status) if legacy else None
        out["IS_CANONICAL_ENFORCED"] = ACCOUNT_ID in parse_account_id_list(
            os.environ.get("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "")
        )

        pending = db.query(func.count(StagedQueueItem.id)).scalar()
        out["PENDING_REAL_SEND_JOBS"] = int(pending or 0)

        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()
        await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
