#!/usr/bin/env python3
"""L13 canary post-recreate verification — read-only DB/Redis; no send/OTP.

May write ONLY its local output JSON beside this script (/tmp when copied).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "_l13_canary_verify.out.json"


def _safe_coverage_meta(raw: str | None) -> dict | None:
    """Parse coverage JSON to operational fields only (no secrets)."""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"present": True, "parse_ok": False}
    if not isinstance(payload, dict):
        return {"present": True, "parse_ok": False}
    return {
        "present": True,
        "parse_ok": True,
        "account_id": payload.get("account_id"),
        "hostname": payload.get("hostname"),
        "platform": payload.get("platform"),
        "updated_at": payload.get("updated_at"),
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

    async def cov():
        # Read-only Redis: ping / exists / llen / get only.
        r = redis.from_url(s.REDIS_URL, decode_responses=True)
        await r.ping()
        out = {}
        for a in (12, 13, 23, 74, 79):
            out[str(a)] = {
                "cov": bool(await r.exists(worker_account_coverage_key("rubika", a))),
                "q": int(await r.llen(f"queue:rubika:{a}")),
            }
        raw13 = await r.get(worker_account_coverage_key("rubika", 13))
        await r.aclose()
        return out, _safe_coverage_meta(raw13)

    obs = []
    for i in range(3):
        ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
        c, cov13_meta = asyncio.run(cov())
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
                "coverage": c,
                "cov13_meta": cov13_meta,
            }
        )
        if i < 2:
            time.sleep(5)

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        legacy = select_legacy_rubika_session_row(db, 13)
        cmp = compare_legacy_vs_canonical(db, 13, legacy_row=legacy)
        active13 = db.execute(
            text(
                "SELECT id FROM channel_sessions "
                "WHERE account_id=13 AND session_status::text='active'"
            )
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

    expected = [12, 13, 79]
    stable = all(o["actual"] == expected for o in obs)
    last = obs[-1]
    coverage = last["coverage"]
    hosts = {
        (o.get("cov13_meta") or {}).get("hostname")
        for o in obs
        if (o.get("cov13_meta") or {}).get("hostname")
    }
    duplicate_13 = len(hosts) > 1

    artifact = {
        "mode": last["mode"],
        "cohort": last["cohort"],
        "pin": last["pin"],
        "dynamic": last["dynamic"],
        "actual": last["actual"],
        "obs": obs,
        "stable": stable,
        "count": len(obs),
        "account13_worker_present": coverage["13"]["cov"] is True
        and 13 in last["actual"],
        "account23_worker_present": coverage["23"]["cov"] is True
        or 23 in last["actual"],
        "account74_worker_present": coverage["74"]["cov"] is True
        or 74 in last["actual"],
        "account12_preserved": coverage["12"]["cov"] is True and 12 in last["actual"],
        "account79_preserved": coverage["79"]["cov"] is True and 79 in last["actual"],
        "duplicate_13": duplicate_13,
        "shadow": cmp.as_safe_dict(),
        "active13": active13,
        "a12": a12,
        "a79": a79,
        "msg": msg,
        "login": login,
        "global_active": active_g,
        "message_sent": False,
        "otp_requested": False,
    }

    blob = json.dumps(artifact, default=str)
    for b in ("DATABASE_URL", "REDIS_URL", "SESSION_SECRET"):
        if b in blob:
            raise RuntimeError(f"secret-like content in artifact: {b}")

    OUT.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                k: artifact[k]
                for k in (
                    "mode",
                    "cohort",
                    "pin",
                    "actual",
                    "dynamic",
                    "stable",
                    "count",
                    "account13_worker_present",
                    "account23_worker_present",
                    "account74_worker_present",
                    "account12_preserved",
                    "account79_preserved",
                    "duplicate_13",
                    "shadow",
                    "active13",
                    "global_active",
                    "msg",
                    "login",
                )
            },
            default=str,
        )
    )
    ok = (
        last["mode"] == "dynamic"
        and last["cohort"] == [13]
        and last["pin"] == [12, 79]
        and last["dynamic"] == [13, 23, 74, 79]
        and stable
        and artifact["account13_worker_present"]
        and not artifact["account23_worker_present"]
        and not artifact["account74_worker_present"]
        and artifact["account12_preserved"]
        and artifact["account79_preserved"]
        and not duplicate_13
        and artifact["shadow"]["metric"] == "SHADOW_MATCH"
        and artifact["shadow"]["legacy_selected_session_id"] == 729
        and artifact["shadow"]["canonical_selected_session_id"] == 729
        and int(active13 or 0) == 729
        and int(active_g) == 3
        and artifact["message_sent"] is False
        and artifact["otp_requested"] is False
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
