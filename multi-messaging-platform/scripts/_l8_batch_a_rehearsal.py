#!/usr/bin/env python3
"""L8 Batch A rehearsal — promote proven legacy sessions on isolated clone ONLY.

Targets (clone only):
  Account13 -> Session729
  Account23 -> Session725
  Account74 -> Session724

Never mutates production mmp_db.
Never enables enforce.
Never OTP / send / worker restart.

Requires:
  L8_ALLOW_REHEARSAL=1
  SESSION_SECRET (same as production — needed to decrypt cloned ciphertext)

Steps:
  1) pg_dump mmp_db (read-only)
  2) restore into mmp_l8_rehearsal
  3) promote 13,23,74 one-by-one with fresh RealRubikaCandidateProver
  4) verify ACTIVE / protected / duplicates
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PRODUCTION_DB = "mmp_db"
REHEARSAL_DB = "mmp_l8_rehearsal"
REHEARSAL_ALLOWLIST = frozenset({REHEARSAL_DB})
PG_USER = os.environ.get("PGUSER", "mmp_user")
PG_PASSWORD = os.environ.get("PGPASSWORD", "mmp_pass")
PG_HOST_CONTAINER = os.environ.get("PGHOST", "mmp_postgres")
PG_SERVICE = os.environ.get("L8_PG_SERVICE_HOSTNAME", "postgres")
COMPOSE_NETWORK = os.environ.get("L8_REHEARSAL_NETWORK", "multi-messaging-platform_default")
RUNNER = "mmp_l8_rehearsal_runner"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "rubika-remediation"
BACKUP_DIR = REPORT_DIR / "l8_rehearsal"
OUT_JSON = REPORT_DIR / "L8_BATCH_A_REHEARSAL.json"
OUT_MD = REPORT_DIR / "L8_CONTROLLED_LEGACY_PROMOTION_PLAN.md"

BATCH_A = (
    (13, 729),
    (23, 725),
    (74, 724),
)
PROTECTED = (12, 79)
PROTECTED_SESSIONS = {12: (657, 728), 79: (600,)}


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def assert_rehearsal_db(name: str) -> str:
    n = str(name or "").strip()
    if n != REHEARSAL_DB or n not in REHEARSAL_ALLOWLIST:
        raise RuntimeError(f"REFUSE: rehearsal target must be exactly {REHEARSAL_DB}")
    if n == PRODUCTION_DB:
        raise RuntimeError("REFUSE: production db")
    return n


def _psql_admin(sql: str) -> str:
    r = _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "psql",
            "-U",
            PG_USER,
            "-d",
            "postgres",
            "-tAc",
            sql,
        ]
    )
    return (r.stdout or "").strip()


def _psql(db: str, sql: str) -> str:
    if db == PRODUCTION_DB:
        upper = sql.strip().upper()
        if any(
            upper.startswith(t)
            for t in (
                "DROP ",
                "CREATE ",
                "ALTER ",
                "TRUNCATE ",
                "INSERT ",
                "UPDATE ",
                "DELETE ",
            )
        ):
            raise RuntimeError("REFUSE: mutation SQL against production")
    elif db != REHEARSAL_DB:
        raise RuntimeError(f"REFUSE: unexpected db {db}")
    r = _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "psql",
            "-U",
            PG_USER,
            "-d",
            db,
            "-tAc",
            sql,
        ]
    )
    return (r.stdout or "").strip()


def backup_production() -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = BACKUP_DIR / f"mmp_db_pre_l8_{ts}.dump"
    _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "pg_dump",
            "-U",
            PG_USER,
            "-Fc",
            "-f",
            f"/tmp/{dump_path.name}",
            PRODUCTION_DB,
        ]
    )
    _run(["docker", "cp", f"{PG_HOST_CONTAINER}:/tmp/{dump_path.name}", str(dump_path)])
    digest = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    meta = {
        "BACKUP_PATH": str(dump_path),
        "BACKUP_SHA256": digest,
        "BACKUP_TIMESTAMP": ts,
        "BACKUP_SIZE": dump_path.stat().st_size,
        "source_db": PRODUCTION_DB,
        "mutation": False,
    }
    (BACKUP_DIR / f"{dump_path.name}.meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    return meta


def restore_clone(dump_path: Path) -> None:
    assert_rehearsal_db(REHEARSAL_DB)
    _psql_admin(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{REHEARSAL_DB}' AND pid <> pg_backend_pid();"
    )
    _psql_admin(f'DROP DATABASE IF EXISTS "{REHEARSAL_DB}";')
    _psql_admin(f'CREATE DATABASE "{REHEARSAL_DB}" OWNER {PG_USER};')
    container_dump = f"/tmp/{dump_path.name}"
    _run(["docker", "cp", str(dump_path), f"{PG_HOST_CONTAINER}:{container_dump}"])
    _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "pg_restore",
            "-U",
            PG_USER,
            "-d",
            REHEARSAL_DB,
            "--no-owner",
            "--no-acl",
            container_dump,
        ],
        check=False,
    )


def _resolve_image() -> str:
    r = _run(
        [
            "docker",
            "inspect",
            "mmp_core_api",
            "--format",
            "{{.Config.Image}}",
        ]
    )
    return (r.stdout or "").strip()


def _session_secret() -> str:
    secret = (os.environ.get("SESSION_SECRET") or "").strip()
    if secret:
        return secret
    r = _run(
        ["docker", "exec", "mmp_core_api", "printenv", "SESSION_SECRET"],
        check=False,
    )
    secret = (r.stdout or "").strip()
    if not secret:
        raise RuntimeError("REFUSE: SESSION_SECRET required for rehearsal decrypt")
    return secret


async def _promote_in_process() -> dict:
    """Run inside runner container — DATABASE_URL already points at rehearsal."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core_engine.services.rubika_canonical_session import load_canonical_rubika_session
    from core_engine.services.rubika_legacy_promotion import (
        LegacyPromotionEvidence,
        promote_proven_legacy_rubika_session,
        verify_post_promotion,
    )
    from tests.isolation import assert_test_database_url

    url = os.environ["DATABASE_URL"]
    # Refuse production name even if mis-set.
    name = assert_test_database_url(url)
    if name != REHEARSAL_DB:
        # isolation helper allows mmp_l* prefixes — still require exact L8 name.
        if name != REHEARSAL_DB:
            raise RuntimeError(f"REFUSE: expected {REHEARSAL_DB}, got {name}")

    engine = create_engine(url)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    results = {"promotions": [], "verifications": [], "protected_check": {}}
    try:
        for aid, sid in BATCH_A:
            print(f"rehearsal promote account={aid} session={sid}", flush=True)
            promo = await promote_proven_legacy_rubika_session(
                db,
                account_id=aid,
                session_id=sid,
                expected_identity=None,
                evidence=LegacyPromotionEvidence(
                    source_phase="L8_REHEARSAL",
                    operator_note="isolated clone only",
                ),
                queue_length_fn=lambda _a: 0,
            )
            results["promotions"].append(promo.as_safe_dict())
            if not promo.ok:
                results["stopped_at"] = {"account_id": aid, "session_id": sid}
                break
            ver = verify_post_promotion(db, account_id=aid, session_id=sid)
            results["verifications"].append(ver)
            # canonical loader exact
            loaded = load_canonical_rubika_session(db, aid)
            results["verifications"][-1]["canonical_loader_session_id"] = int(
                loaded.session_id
            )

        # Protected + non-target ACTIVE
        from sqlalchemy import text

        active_non_target = db.execute(
            text(
                "SELECT account_id, id FROM channel_sessions "
                "WHERE session_status::text='active' "
                "AND account_id NOT IN (13,23,74) ORDER BY account_id, id"
            )
        ).fetchall()
        results["non_target_active"] = [
            {"account_id": int(a), "session_id": int(s)} for a, s in active_non_target
        ]
        for aid in PROTECTED:
            rows = db.execute(
                text(
                    "SELECT id, session_status::text FROM channel_sessions "
                    "WHERE account_id=:a AND session_type::text IN "
                    "('rubika_session','RUBIKA_SESSION') ORDER BY id"
                ),
                {"a": aid},
            ).fetchall()
            results["protected_check"][str(aid)] = [
                {"session_id": int(r[0]), "status": r[1]} for r in rows
            ]
        dup = db.execute(
            text(
                "SELECT account_id, COUNT(*) FROM channel_sessions "
                "WHERE session_type::text IN ('rubika_session','RUBIKA_SESSION') "
                "GROUP BY account_id HAVING COUNT(*)>1 ORDER BY account_id"
            )
        ).fetchall()
        results["duplicate_accounts"] = [int(r[0]) for r in dup]
        results["active_total"] = int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM channel_sessions "
                    "WHERE session_status::text='active'"
                )
            ).scalar()
            or 0
        )
    finally:
        db.close()
        engine.dispose()
    return results


def run_runner() -> dict:
    secret = _session_secret()
    image = _resolve_image()
    _run(["docker", "rm", "-f", RUNNER], check=False)
    database_url = (
        f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_SERVICE}:5432/{REHEARSAL_DB}"
    )
    # Inline python to call _promote_in_process via module path
    code = (
        "import asyncio, json, os, sys;\n"
        "sys.path.insert(0, '/app');\n"
        "os.environ['DATABASE_URL'] = os.environ['DATABASE_URL'];\n"
        "from scripts._l8_batch_a_rehearsal import _promote_in_process;\n"
        "print(json.dumps(asyncio.run(_promote_in_process())));\n"
    )
    create = _run(
        [
            "docker",
            "create",
            "--name",
            RUNNER,
            "--network",
            COMPOSE_NETWORK,
            "-e",
            f"DATABASE_URL={database_url}",
            "-e",
            f"SESSION_SECRET={secret}",
            "-e",
            "RUBIKA_CANONICAL_SESSION_MODE=off",
            "-e",
            "RUBIKA_CANONICAL_SESSION_V1=false",
            "-e",
            "CONTROLLED_PRODUCTION_ENABLED=false",
            "-e",
            "REAL_MESSAGE_SENDING_ENABLED=false",
            "-v",
            f"{PROJECT_ROOT}:/app",
            "-w",
            "/app",
            image,
            "python",
            "-c",
            code,
        ]
    )
    if create.returncode != 0:
        raise RuntimeError(create.stderr)
    started = _run(["docker", "start", "-a", RUNNER], check=False)
    _run(["docker", "rm", "-f", RUNNER], check=False)
    out = (started.stdout or "").strip()
    # Last JSON object line
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and "promotions" in line:
            return json.loads(line)
    raise RuntimeError(f"rehearsal runner failed: {started.stderr}\n{out[-2000:]}")


def write_plan(rehearsal: dict, backup: dict) -> None:
    promo_ok = all(p.get("ok") for p in rehearsal.get("promotions") or [])
    non_target = rehearsal.get("non_target_active") or []
    lines = [
        "# L8 — Controlled Legacy Promotion Plan (Batch A)",
        "",
        "**PHASE_STATUS: BLOCKED pending operator approval for production**",
        "",
        "## Implemented",
        "",
        "- `promote_proven_legacy_rubika_session` (legacy → prove → bind → VALIDATING → `promote_validated_session`)",
        "- Exact-ID `rollback_legacy_promotion`",
        "- Isolated tests + clone rehearsal",
        "",
        "## Rehearsal summary",
        "",
        f"- backup: `{backup.get('BACKUP_PATH')}`",
        f"- sha256: `{backup.get('BACKUP_SHA256')}`",
        f"- rehearsal promotions ok: `{promo_ok}`",
        f"- non-target ACTIVE count: `{len(non_target)}`",
        f"- active_total on clone: `{rehearsal.get('active_total')}`",
        "",
        "## Production execution order (NOT executed yet)",
        "",
        "1. Fresh production backup",
        "2. Account13 / Session729 — promote + verify + STOP",
        "3. Account23 / Session725 — promote + verify + STOP",
        "4. Account74 / Session724 — promote + verify + STOP",
        "5. Only then set `RUBIKA_CANONICAL_SESSION_MODE=shadow` with allowlist `13,23,74`",
        "6. Observe SHADOW_MATCH; do **not** enable enforce",
        "",
        "## Hard exclusions",
        "",
        "- No Account2 / 12 / 79 / 1 / 19 / 81 / 92",
        "- No global enforce",
        "- No OTP / send / worker restart during promotion",
        "",
        f"**SAFE_TO_PROMOTE_BATCH_A_PRODUCTION** (after rehearsal): `{promo_ok and len(non_target)==0}`",
        "",
        "**EXACT_OPERATOR_INPUT_REQUIRED:** Approve production promotion of Account13/23/74 one at a time after rehearsal",
        "",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if os.environ.get("L8_ALLOW_REHEARSAL") != "1":
        print("REFUSE: set L8_ALLOW_REHEARSAL=1", file=sys.stderr)
        return 2
    if os.environ.get("L8_INNER_PROMOTE") == "1":
        # Called only inside runner
        payload = asyncio.run(_promote_in_process())
        print(json.dumps(payload))
        return 0

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    backup = backup_production()
    restore_clone(Path(backup["BACKUP_PATH"]))
    # Prefer exec via runner script entry that avoids embedding secret in -c if possible
    secret = _session_secret()
    image = _resolve_image()
    _run(["docker", "rm", "-f", RUNNER], check=False)
    database_url = (
        f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_SERVICE}:5432/{REHEARSAL_DB}"
    )
    create = _run(
        [
            "docker",
            "create",
            "--name",
            RUNNER,
            "--network",
            COMPOSE_NETWORK,
            "-e",
            f"DATABASE_URL={database_url}",
            "-e",
            f"SESSION_SECRET={secret}",
            "-e",
            "L8_ALLOW_REHEARSAL=1",
            "-e",
            "L8_INNER_PROMOTE=1",
            "-e",
            "RUBIKA_CANONICAL_SESSION_MODE=off",
            "-e",
            "RUBIKA_CANONICAL_SESSION_V1=false",
            "-e",
            "CONTROLLED_PRODUCTION_ENABLED=false",
            "-e",
            "REAL_MESSAGE_SENDING_ENABLED=false",
            "-e",
            "PYTHONPATH=/app",
            "-v",
            f"{str(PROJECT_ROOT)}:/app",
            "-w",
            "/app",
            image,
            "python",
            "scripts/_l8_batch_a_rehearsal.py",
        ]
    )
    if create.returncode != 0:
        print(create.stderr, file=sys.stderr)
        return 2
    started = _run(["docker", "start", "-a", RUNNER], check=False)
    _run(["docker", "rm", "-f", RUNNER], check=False)
    out = (started.stdout or "").strip()
    rehearsal = None
    for line in reversed(out.splitlines()):
        if line.strip().startswith("{") and "promotions" in line:
            rehearsal = json.loads(line.strip())
            break
    if rehearsal is None:
        print(started.stderr, file=sys.stderr)
        print(out[-4000:], file=sys.stderr)
        return 2

    artifact = {
        "phase": "L8_CONTROLLED_LEGACY_PROMOTION",
        "phase_status": "BLOCKED",
        "production_promotions_executed": 0,
        "rubika_canonical_session_mode_production": "off",
        "backup": backup,
        "rehearsal_db": REHEARSAL_DB,
        "rehearsal": rehearsal,
        "L8_REHEARSAL_ACCOUNT13_PASS": any(
            p.get("account_id") == 13 and p.get("ok")
            for p in rehearsal.get("promotions") or []
        ),
        "L8_REHEARSAL_ACCOUNT23_PASS": any(
            p.get("account_id") == 23 and p.get("ok")
            for p in rehearsal.get("promotions") or []
        ),
        "L8_REHEARSAL_ACCOUNT74_PASS": any(
            p.get("account_id") == 74 and p.get("ok")
            for p in rehearsal.get("promotions") or []
        ),
        "REHEARSAL_NON_TARGET_ACTIVE_COUNT": len(rehearsal.get("non_target_active") or []),
    }
    promo_ok = all(p.get("ok") for p in rehearsal.get("promotions") or []) and len(
        rehearsal.get("promotions") or []
    ) == 3
    artifact["SAFE_TO_PROMOTE_BATCH_A_PRODUCTION"] = bool(
        promo_ok and artifact["REHEARSAL_NON_TARGET_ACTIVE_COUNT"] == 0
    )
    OUT_JSON.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    write_plan(rehearsal, backup)
    print(json.dumps({k: artifact[k] for k in (
        "L8_REHEARSAL_ACCOUNT13_PASS",
        "L8_REHEARSAL_ACCOUNT23_PASS",
        "L8_REHEARSAL_ACCOUNT74_PASS",
        "REHEARSAL_NON_TARGET_ACTIVE_COUNT",
        "SAFE_TO_PROMOTE_BATCH_A_PRODUCTION",
        "production_promotions_executed",
    )}, indent=2))
    return 0 if artifact["SAFE_TO_PROMOTE_BATCH_A_PRODUCTION"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
