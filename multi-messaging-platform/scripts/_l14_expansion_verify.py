#!/usr/bin/env python3
"""L14 post-expansion verification — read-only DB/Redis; no send/OTP."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "_l14_expansion_verify.out.json"
EXPECTED = [12, 13, 23, 74, 79]


def _safe_cov_meta(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        p = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"present": True, "parse_ok": False}
    if not isinstance(p, dict):
        return {"present": True, "parse_ok": False}
    return {
        "present": True,
        "parse_ok": True,
        "account_id": p.get("account_id"),
        "hostname": p.get("hostname"),
        "platform": p.get("platform"),
        "updated_at": p.get("updated_at"),
    }


def main() -> int:
    import redis.asyncio as redis
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.services.rubika_canonical_runtime import (
        compare_legacy_vs_canonical,
        select_legacy_rubika_session_row,
    )
    from workers.config import get_worker_settings
    from workers.redis_keys import worker_account_coverage_key
    from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings

    s = get_worker_settings()

    async def snapshot_cov():
        r = redis.from_url(s.REDIS_URL, decode_responses=True)
        await r.ping()
        out = {}
        metas = {}
        for a in EXPECTED + [99]:  # 99 unused sentinel skip
            if a == 99:
                continue
            key = worker_account_coverage_key("rubika", a)
            out[str(a)] = {
                "cov": bool(await r.exists(key)),
                "q": int(await r.llen(f"queue:rubika:{a}")),
            }
            metas[str(a)] = _safe_cov_meta(await r.get(key))
        await r.aclose()
        return out, metas

    obs = []
    for i in range(3):
        ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
        cov, metas = asyncio.run(snapshot_cov())
        cohort = [
            int(x)
            for x in (s.RUBIKA_WORKER_DISCOVERY_COHORT_IDS or "").split(",")
            if x.strip()
        ]
        obs.append(
            {
                "actual": ids,
                "dynamic": meta["dynamic_eligible_ids"],
                "mode": meta["mode"],
                "cohort": cohort,
                "pin": meta["pinned_ids"],
                "coverage": cov,
                "cov_meta": {k: metas[k] for k in ("13", "23", "74")},
            }
        )
        if i < 2:
            time.sleep(5)

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        shadows = {}
        actives = {}
        for aid in (13, 23, 74):
            legacy = select_legacy_rubika_session_row(db, aid)
            cmp = compare_legacy_vs_canonical(db, aid, legacy_row=legacy)
            shadows[str(aid)] = cmp.as_safe_dict()
            actives[str(aid)] = db.execute(
                text(
                    "SELECT id FROM channel_sessions "
                    "WHERE account_id=:aid AND session_status::text='active'"
                ),
                {"aid": aid},
            ).scalar()
        a12 = db.execute(
            text(
                "SELECT string_agg(id::text || ':' || session_status::text, ',' "
                "ORDER BY id) FROM channel_sessions WHERE account_id=12"
            )
        ).scalar()
        a79 = db.execute(
            text(
                "SELECT string_agg(id::text || ':' || session_status::text, ',' "
                "ORDER BY id) FROM channel_sessions WHERE account_id=79"
            )
        ).scalar()
        msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
        login = int(
            db.execute(text("SELECT count(*) FROM rubika_login_challenges")).scalar()
            or 0
        )
        active_g = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        )
    finally:
        db.rollback()
        db.close()

    stable = all(o["actual"] == EXPECTED for o in obs)
    last = obs[-1]
    cov = last["coverage"]

    dups = {}
    for aid in ("13", "23", "74"):
        hosts = set()
        for o in obs:
            h = ((o.get("cov_meta") or {}).get(aid) or {}).get("hostname")
            if h:
                hosts.add(h)
        dups[aid] = len(hosts) > 1

    artifact = {
        "mode": last["mode"],
        "cohort": last["cohort"],
        "pin": last["pin"],
        "dynamic": last["dynamic"],
        "actual": last["actual"],
        "obs": obs,
        "stable": stable,
        "count": len(obs),
        "shadows": shadows,
        "actives": actives,
        "a12": a12,
        "a79": a79,
        "msg": msg,
        "login": login,
        "global_active": active_g,
        "dups": dups,
        "account12_preserved": cov["12"]["cov"] and 12 in last["actual"],
        "account79_preserved": cov["79"]["cov"] and 79 in last["actual"],
        "workers": {
            str(a): cov[str(a)]["cov"] and a in last["actual"] for a in EXPECTED
        },
        "message_sent": False,
        "otp_requested": False,
    }
    blob = json.dumps(artifact, default=str)
    for b in ("DATABASE_URL", "REDIS_URL", "SESSION_SECRET"):
        if b in blob:
            raise RuntimeError(f"secret leak: {b}")

    OUT.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    summary = {
        "mode": artifact["mode"],
        "cohort": artifact["cohort"],
        "actual": artifact["actual"],
        "dynamic": artifact["dynamic"],
        "stable": stable,
        "count": len(obs),
        "workers": artifact["workers"],
        "dups": dups,
        "shadows": {
            k: {"metric": v["metric"], "legacy": v["legacy_selected_session_id"], "canon": v["canonical_selected_session_id"]}
            for k, v in shadows.items()
        },
        "actives": actives,
        "global_active": active_g,
        "msg": msg,
        "login": login,
        "account12_preserved": artifact["account12_preserved"],
        "account79_preserved": artifact["account79_preserved"],
    }
    print(json.dumps(summary, default=str))

    ok = (
        last["mode"] == "dynamic"
        and last["cohort"] == [13, 23, 74]
        and last["pin"] == [12, 79]
        and last["dynamic"] == [13, 23, 74, 79]
        and stable
        and all(artifact["workers"][str(a)] for a in EXPECTED)
        and not any(dups.values())
        and shadows["13"]["metric"] == "SHADOW_MATCH"
        and shadows["23"]["metric"] == "SHADOW_MATCH"
        and shadows["74"]["metric"] == "SHADOW_MATCH"
        and int(actives["13"] or 0) == 729
        and int(actives["23"] or 0) == 725
        and int(actives["74"] or 0) == 724
        and a12 == "657:legacy_unclassified,728:legacy_unclassified"
        and a79 == "600:legacy_unclassified"
        and active_g == 3
        and artifact["message_sent"] is False
        and artifact["otp_requested"] is False
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
