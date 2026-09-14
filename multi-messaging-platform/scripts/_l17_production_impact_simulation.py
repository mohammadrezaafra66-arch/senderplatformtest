#!/usr/bin/env python3
"""L17 production READ-ONLY impact simulation. Never mutates production."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "reports" / "rubika-remediation" / "L17_PRODUCTION_IMPACT_SIMULATION.json"
OUT_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "_l17_production_impact_simulation.out.json"


def main() -> int:
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.models import Account, PlatformType
    from core_engine.services.rubika_l17_automation import DISCOVERY_SCOPE_ALL_ELIGIBLE
    from workers.rubika_account_pool import resolve_current_phase
    from workers.rubika_worker_discovery import (
        MODE_DYNAMIC,
        get_dispatch_eligible_rubika_account_ids,
        resolve_actual_worker_account_ids,
    )

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        from core_engine.services.rubika_l17_automation import (
            DISCOVERY_SCOPE_ALL_ELIGIBLE,
            account_allows_l3_retry_debris,
            account_has_zero_sessions,
            account_is_canonical_managed,
            account_is_legacy_protected,
        )

        accounts = (
            db.query(Account)
            .filter(Account.platform == PlatformType.RUBIKA)
            .order_by(Account.id.asc())
            .all()
        )
        auto_l3 = []
        auto_enforce = []
        legacy_protected = []
        for a in accounts:
            # Permanent evidence rule (ignore live pilot/V1 config for impact calc).
            zero = account_has_zero_sessions(db, a.id)
            managed = account_is_canonical_managed(db, a.id)
            protected = account_is_legacy_protected(db, a.id)
            debris_l3 = account_allows_l3_retry_debris(db, a.id)
            if zero or managed or debris_l3:
                auto_l3.append(int(a.id))
            if managed:
                auto_enforce.append(int(a.id))
            if protected:
                legacy_protected.append(int(a.id))

        phase = resolve_current_phase(db)
        dynamic = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
        resulting = resolve_actual_worker_account_ids(
            mode=MODE_DYNAMIC,
            pinned_ids=[12, 79],
            dynamic_eligible_ids=dynamic,
            cohort_ids=[],
            discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE,
        )

        unsafe_enforce = [x for x in auto_enforce if x in {12, 79}]
        pin_ok = 12 in resulting and 79 in resulting
        expected_enforce = {13, 23, 27, 74}
        enforce_ok = expected_enforce.issubset(set(auto_enforce)) and not unsafe_enforce

        art = {
            "READ_ONLY": True,
            "phase": phase,
            "AUTO_L3_ACCOUNT_IDS": auto_l3,
            "AUTO_CANONICAL_ENFORCE_ACCOUNT_IDS": auto_enforce,
            "LEGACY_PROTECTED_ACCOUNT_IDS": legacy_protected,
            "DYNAMIC_ELIGIBLE_IDS": dynamic,
            "RESULTING_WORKER_IDS": resulting,
            "ACCOUNT12_PROTECTED": 12 in legacy_protected and 12 not in auto_enforce,
            "ACCOUNT79_PROTECTED": 79 in legacy_protected and 79 not in auto_enforce,
            "WORKER_PIN_12_79_PRESERVED": pin_ok,
            "EXPECTED_ENFORCE_SUBSET_OK": enforce_ok,
            "UNSAFE_ENFORCE_IDS": unsafe_enforce,
            "PRODUCTION_IMPACT_SIMULATION_PASS": enforce_ok and pin_ok and not unsafe_enforce,
            "NEW_ACCOUNT_SCHEDULE_AUTOMATION_REQUIRED": False,
            "SCHEDULE_NOTE": (
                "RubikaSenderSchedule is global phase windows (Iran TZ), not per-account. "
                "Dispatch eligibility requires being inside an active window; "
                "auto-creating business schedules is intentionally out of scope."
            ),
        }
    finally:
        db.rollback()
        db.close()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(art, indent=2)
    try:
        REPORT.write_text(payload, encoding="utf-8")
    except OSError:
        pass
    OUT_SCRIPTS.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if art["PRODUCTION_IMPACT_SIMULATION_PASS"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
