#!/usr/bin/env python3
"""L15 predeploy + stage verification helpers (read-only DB/Redis)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from sqlalchemy import text

EXPECTED_WORKERS = [12, 13, 23, 74, 79]
BATCH = {13: 729, 23: 725, 74: 724}


def _cov_snapshot(redis_url: str) -> dict:
    import redis.asyncio as redis
    from workers.redis_keys import worker_account_coverage_key

    async def _run():
        r = redis.from_url(redis_url, decode_responses=True)
        await r.ping()
        out = {}
        for a in EXPECTED_WORKERS:
            key = worker_account_coverage_key("rubika", a)
            out[str(a)] = {
                "cov": bool(await r.exists(key)),
                "q": int(await r.llen(f"queue:rubika:{a}")),
            }
        await r.aclose()
        return out

    return asyncio.run(_run())


def predeploy() -> dict:
    from core_engine.database import SessionLocal
    from core_engine.services.rubika_canonical_runtime import (
        compare_legacy_vs_canonical,
        select_legacy_rubika_session_row,
    )
    from workers.config import get_worker_settings
    from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings

    s = get_worker_settings()
    ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        shadows = {}
        for aid, sid in BATCH.items():
            legacy = select_legacy_rubika_session_row(db, aid)
            cmp = compare_legacy_vs_canonical(db, aid, legacy_row=legacy)
            shadows[str(aid)] = cmp.as_safe_dict()
        active_g = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        )
        msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
        login = int(
            db.execute(text("SELECT count(*) FROM rubika_login_challenges")).scalar() or 0
        )
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
    finally:
        db.rollback()
        db.close()
    cov = _cov_snapshot(s.REDIS_URL)
    return {
        "discovery_mode": meta["mode"],
        "cohort": meta["cohort_ids"],
        "pin": meta["pinned_ids"],
        "actual": ids,
        "canonical_mode": s.RUBIKA_CANONICAL_SESSION_MODE,
        "canonical_allow": s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS,
        "shadows": shadows,
        "global_active": active_g,
        "msg": msg,
        "login": login,
        "a12": a12,
        "a79": a79,
        "coverage": cov,
    }


async def _reconnect_prove(db, account_id: int, session_id: int):
    from core_engine.services.rubika_candidate_prover import RealRubikaCandidateProver

    proof = await RealRubikaCandidateProver().prove_detailed(
        db,
        account_id=account_id,
        session_id=session_id,
        expected_guid=None,
    )
    return {
        "ok": bool(proof.ok),
        "code": proof.error_code or proof.proof_status,
        "identity_match": proof.identity_match is True,
        "duration_ms": proof.duration_ms,
        "sanitized_message": proof.sanitized_message,
    }


def verify_enforce_stage(
    *,
    allowlist: list[int],
    cycles: int = 3,
    msg_baseline: int | None = None,
    login_baseline: int | None = None,
) -> dict:
    """Verify ENFORCE stage: allowlisted use canonical_enforce source."""
    from core_engine.config import get_settings
    from core_engine.database import SessionLocal
    from core_engine.models import SessionType
    from core_engine.services.rubika_canonical_runtime import (
        load_rubika_runtime_session,
        compare_legacy_vs_canonical,
        select_legacy_rubika_session_row,
    )
    from workers.config import get_worker_settings
    from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings
    from workers.session_access import load_account_session_plaintext

    # Clear settings caches so process-local/env reflects container env.
    get_settings.cache_clear()
    try:
        from workers.config import get_worker_settings as gws

        gws.cache_clear()
    except Exception:  # noqa: BLE001
        pass

    s = get_worker_settings()
    allow = set(allowlist)
    obs = []
    for i in range(cycles):
        ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
        cov = _cov_snapshot(s.REDIS_URL)
        obs.append({"actual": ids, "coverage": cov, "mode": meta["mode"], "cohort": meta["cohort_ids"]})
        if i < cycles - 1:
            time.sleep(4)

    db = SessionLocal()
    results = {}
    try:
        # Ensure core settings also see enforce (same process as worker when run in worker).
        get_settings.cache_clear()
        for aid, sid in BATCH.items():
            legacy = select_legacy_rubika_session_row(db, aid)
            cmp = compare_legacy_vs_canonical(db, aid, legacy_row=legacy)
            selected = load_rubika_runtime_session(db, aid)
            # session_access / dispatch readiness (decrypt via canonical path)
            dispatch_ok = False
            dispatch_err = None
            try:
                pt = load_account_session_plaintext(
                    db, account_id=aid, session_type=SessionType.RUBIKA_SESSION
                )
                dispatch_ok = isinstance(pt, (bytes, bytearray)) and len(pt) > 0
                del pt
            except Exception as exc:  # noqa: BLE001
                dispatch_ok = False
                dispatch_err = type(exc).__name__
            # Discard plaintext immediately.
            _ = len(selected.plaintext) if selected.plaintext is not None else 0
            recon = None
            if aid in allow:
                recon = asyncio.run(_reconnect_prove(db, aid, int(selected.session_id)))
            results[str(aid)] = {
                "in_enforce": aid in allow,
                "runtime_session_id": selected.session_id,
                "runtime_source": selected.source,
                "runtime_mode": selected.mode,
                "shadow": cmp.as_safe_dict(),
                "expected_session": sid,
                "dispatch_readiness": dispatch_ok,
                "dispatch_err": dispatch_err,
                "reconnect": recon,
            }
        # Read-only inventory after probes; always rollback.
        active_g = int(
            db.execute(
                text(
                    "SELECT count(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        )
        msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
        login = int(
            db.execute(text("SELECT count(*) FROM rubika_login_challenges")).scalar() or 0
        )
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
    finally:
        db.rollback()
        db.close()

    last = obs[-1]
    stable = all(o["actual"] == EXPECTED_WORKERS for o in obs)
    account_ok = {}
    for aid, sid in BATCH.items():
        row = results[str(aid)]
        if aid in allow:
            recon = row.get("reconnect") or {}
            account_ok[str(aid)] = (
                row["runtime_source"] == "canonical_enforce"
                and row["runtime_mode"] == "enforce"
                and int(row["runtime_session_id"]) == sid
                and row["shadow"]["metric"] == "SHADOW_MATCH"
                and last["coverage"][str(aid)]["cov"] is True
                and row["dispatch_readiness"] is True
                and recon.get("ok") is True
                and recon.get("identity_match") is True
            )
        else:
            account_ok[str(aid)] = (
                row["runtime_source"] == "legacy"
                and int(row["runtime_session_id"]) == sid
                and last["coverage"][str(aid)]["cov"] is True
                and row["dispatch_readiness"] is True
            )

    counts_ok = True
    if msg_baseline is not None:
        counts_ok = counts_ok and msg == msg_baseline
    if login_baseline is not None:
        counts_ok = counts_ok and login == login_baseline

    ok = (
        s.RUBIKA_CANONICAL_SESSION_MODE == "enforce"
        and sorted(
            int(x)
            for x in (s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS or "").split(",")
            if x.strip()
        )
        == sorted(allowlist)
        and last["mode"] == "dynamic"
        and last["cohort"] == [13, 23, 74]
        and stable
        and all(account_ok.values())
        and last["coverage"]["12"]["cov"]
        and last["coverage"]["79"]["cov"]
        and a12 == "657:legacy_unclassified,728:legacy_unclassified"
        and a79 == "600:legacy_unclassified"
        and active_g == 3
        and counts_ok
        and all(last["coverage"][str(a)]["q"] == 0 for a in EXPECTED_WORKERS)
    )
    return {
        "ok": ok,
        "canonical_mode": s.RUBIKA_CANONICAL_SESSION_MODE,
        "canonical_allow": s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS,
        "allowlist": allowlist,
        "obs": obs,
        "stable": stable,
        "accounts": results,
        "account_ok": account_ok,
        "actual": last["actual"],
        "coverage": last["coverage"],
        "global_active": active_g,
        "msg": msg,
        "login": login,
        "msg_baseline": msg_baseline,
        "login_baseline": login_baseline,
        "counts_ok": counts_ok,
        "a12": a12,
        "a79": a79,
        "message_sent": False,
        "otp_requested": False,
    }


def main() -> int:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "predeploy").strip().lower()
    if cmd == "predeploy":
        data = predeploy()
        print(json.dumps(data, default=str))
        ok = (
            data["canonical_mode"] == "shadow"
            and data["canonical_allow"] == "13,23,74"
            and data["discovery_mode"] == "dynamic"
            and data["cohort"] == [13, 23, 74]
            and data["pin"] == [12, 79]
            and data["actual"] == EXPECTED_WORKERS
            and data["global_active"] == 3
            and all(data["shadows"][str(a)]["metric"] == "SHADOW_MATCH" for a in BATCH)
            and all(data["coverage"][str(a)]["cov"] for a in EXPECTED_WORKERS)
            and all(data["coverage"][str(a)]["q"] == 0 for a in EXPECTED_WORKERS)
        )
        return 0 if ok else 2
    if cmd.startswith("verify:"):
        allow = [int(x) for x in cmd.split(":", 1)[1].split(",") if x.strip()]
        msg_b = int(sys.argv[2]) if len(sys.argv) > 2 else None
        login_b = int(sys.argv[3]) if len(sys.argv) > 3 else None
        data = verify_enforce_stage(
            allowlist=allow, msg_baseline=msg_b, login_baseline=login_b
        )
        out = Path(__file__).resolve().parent / f"_l15_verify_{'-'.join(map(str, allow))}.out.json"
        out.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(json.dumps({k: data[k] for k in (
            "ok","canonical_mode","canonical_allow","allowlist","stable","actual",
            "account_ok","global_active","msg","login","counts_ok"
        )}, default=str))
        # also print per-account runtime sources
        for aid, row in data["accounts"].items():
            recon = row.get("reconnect") or {}
            print(
                f"ACCT_{aid}_source={row['runtime_source']} sess={row['runtime_session_id']} "
                f"mode={row['runtime_mode']} dispatch={row.get('dispatch_readiness')} "
                f"recon_ok={recon.get('ok')} id_match={recon.get('identity_match')} "
                f"ok={data['account_ok'][aid]}"
            )
        return 0 if data["ok"] else 2
    print("usage: predeploy | verify:13 | verify:13,23 | verify:13,23,74")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
