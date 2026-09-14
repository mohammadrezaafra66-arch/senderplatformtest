#!/usr/bin/env python3
"""One-shot read-only L16 candidate diagnostic."""
import json
from sqlalchemy import text
from core_engine.database import SessionLocal
from core_engine.models import AccountStatus, RubikaAccountPool
from workers.rubika_account_pool import resolve_current_phase

EXCLUDED = {2, 12, 13, 23, 74, 79}
db = SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    phase = resolve_current_phase(db)
    pool_ids = (
        {int(r.account_id) for r in db.query(RubikaAccountPool).filter(RubikaAccountPool.phase == phase).all()}
        if phase
        else set()
    )
    rows = db.execute(
        text(
            """
        SELECT a.id, a.status::text AS status,
               (SELECT count(*) FROM channel_sessions cs WHERE cs.account_id=a.id) AS sess_n,
               a.phone_number IS NOT NULL AND trim(a.phone_number) <> '' AS has_phone,
               a.rubika_guid IS NOT NULL AND trim(a.rubika_guid) <> '' AS has_guid
        FROM accounts a
        WHERE a.platform::text='rubika'
        ORDER BY a.id
        """
        )
    ).mappings().all()
    out = []
    for r in rows:
        aid = int(r["id"])
        reasons = []
        if aid in EXCLUDED:
            reasons.append("EXCLUDED_ID")
        if int(r["sess_n"]) != 0:
            reasons.append(f"HAS_SESSION_{r['sess_n']}")
        if r["status"] != AccountStatus.ACTIVE.value:
            reasons.append(f"STATUS_{r['status']}")
        if not r["has_phone"]:
            reasons.append("NO_PHONE")
        if r["has_guid"]:
            reasons.append("HAS_GUID")
        if phase and aid not in pool_ids:
            reasons.append("NOT_IN_POOL")
        out.append({"id": aid, "sess": int(r["sess_n"]), "blockers": reasons})
    zero = [x for x in out if x["sess"] == 0]
    print(
        json.dumps(
            {
                "phase": phase,
                "total_rubika": len(out),
                "zero_session": len(zero),
                "zero_non_excluded": [x for x in zero if "EXCLUDED_ID" not in x["blockers"]],
                "eligible": [x for x in out if not x["blockers"]],
                "near_miss_zero_sess": [x for x in zero if x["blockers"] == ["EXCLUDED_ID"]][:10],
            },
            indent=2,
        )
    )
finally:
    db.rollback()
    db.close()
