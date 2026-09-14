#!/usr/bin/env python3
"""M1 pre-audit sentinel capture — SELECT-only, fail-closed READ ONLY.

Writes stdout JSON and optionally reports/rubika-remediation/M1_PRE_AUDIT_SENTINELS.json.
Never mutates DB/Redis/sessions/login/messages/workers/config.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal
from core_engine.services.rubika_l17_automation import DISCOVERY_SCOPE_ALL_ELIGIBLE
from workers.rubika_worker_discovery import (
    MODE_DYNAMIC,
    get_dispatch_eligible_rubika_account_ids,
    resolve_actual_worker_account_ids,
)

TARGETS = [2, 12, 19, 81, 92]
EXPECTED_ACTIVES = {13: 729, 23: 725, 27: 772, 74: 724}
EXPECTED_WORKERS = [12, 13, 23, 27, 74, 79]
REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "rubika-remediation"
    / "M1_PRE_AUDIT_SENTINELS.json"
)


def _arm_read_only(db) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    db.autoflush = False

    def _forbid(*_a, **_k):
        raise RuntimeError("M1_SENTINEL_WRITE_FORBIDDEN")

    db.commit = _forbid  # type: ignore[method-assign]
    db.flush = _forbid  # type: ignore[method-assign]
    db.add = _forbid  # type: ignore[method-assign]
    db.delete = _forbid  # type: ignore[method-assign]
    db.merge = _forbid  # type: ignore[method-assign]
    db.execute(text("SET TRANSACTION READ ONLY"))


def _l17_env_snapshot() -> dict:
    keys = (
        "RUBIKA_L3_LOGIN_ROUTING",
        "AUTO_ENROLL_RUBIKA_POOL",
        "RUBIKA_WORKER_DISCOVERY_SCOPE",
        "RUBIKA_CANONICAL_SESSION_SCOPE",
        "RUBIKA_CANONICAL_SESSION_MODE",
        "RUBIKA_CANONICAL_ACCOUNT_ALLOWLIST",
        "RUBIKA_WORKER_DISCOVERY_MODE",
        "RUBIKA_ACCOUNT_IDS",
    )
    return {k: os.environ.get(k) for k in keys}


def main() -> int:
    db = SessionLocal()
    try:
        _arm_read_only(db)

        actives = {
            int(a): int(i)
            for a, i in db.execute(
                text(
                    "SELECT account_id, id FROM channel_sessions "
                    "WHERE session_status='active' ORDER BY account_id"
                )
            ).fetchall()
        }
        dyn = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
        workers = resolve_actual_worker_account_ids(
            mode=MODE_DYNAMIC,
            pinned_ids=[12, 79],
            dynamic_eligible_ids=dyn,
            cohort_ids=[],
            discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
        )
        targets = {}
        target_challenges = {}
        for aid in TARGETS:
            targets[aid] = [
                {"id": int(sid), "status": st}
                for sid, st in db.execute(
                    text(
                        "SELECT id, session_status::text FROM channel_sessions "
                        "WHERE account_id=:a ORDER BY id"
                    ),
                    {"a": aid},
                ).fetchall()
            ]
            target_challenges[aid] = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM rubika_login_challenges "
                        "WHERE account_id=:a"
                    ),
                    {"a": aid},
                ).scalar()
                or 0
            )

        msg = int(db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0)
        ch = int(
            db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar() or 0
        )
        sess_rows = int(db.execute(text("SELECT COUNT(*) FROM channel_sessions")).scalar() or 0)

        out = {
            "GLOBAL_ACTIVE_SESSION_COUNT": len(actives),
            "ACTIVES": actives,
            "EXPECTED_ACTIVES": EXPECTED_ACTIVES,
            "ACTIVES_MATCH_EXPECTED": actives == EXPECTED_ACTIVES,
            "ACTUAL_WORKER_IDS": workers,
            "EXPECTED_WORKERS": EXPECTED_WORKERS,
            "WORKERS_MATCH_EXPECTED": workers == EXPECTED_WORKERS,
            "msg": msg,
            "ch": ch,
            "sess_rows": sess_rows,
            "target_sessions": targets,
            "target_login_challenge_counts": target_challenges,
            "target_queue_counts": {
                "note": "queue depth not queried via mutating commands; sentinel is DB/env only",
                "targets": TARGETS,
            },
            "L17_ENV": _l17_env_snapshot(),
            "L18_NOTE": "L18 status pipeline code unchanged by this sentinel (read-only)",
            "M1_PRE_AUDIT_SENTINELS_CAPTURED": True,
            "DB_READ_ONLY_ENFORCED": True,
            "MESSAGE_SENT": False,
            "OTP_REQUESTED": False,
        }
        text_out = json.dumps(out, default=str, ensure_ascii=False, indent=2) + "\n"
        print(text_out, end="")
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(text_out, encoding="utf-8")
        return 0
    finally:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
