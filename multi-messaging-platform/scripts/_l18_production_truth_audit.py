#!/usr/bin/env python3
"""L18 read-only production truth inventory for all accounts. Never mutates."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "rubika-remediation"
INV = REPORT_DIR / "L18_FINAL_ACCOUNT_TRUTH_INVENTORY.json"
MATRIX = REPORT_DIR / "L18_UI_BACKEND_MISMATCH_MATRIX.json"
AUDIT_MD = REPORT_DIR / "L18_FINAL_ACCOUNT_TRUTH_AUDIT.md"
RECON_MD = REPORT_DIR / "L18_FINAL_PRODUCTION_RECONCILIATION.md"


def main() -> int:
    from sqlalchemy import text

    from core_engine.database import SessionLocal
    from core_engine.models import Account, AccountStatus, ChannelSession, RubikaLoginChallenge
    from core_engine.services.account_runtime_status import (
        RuntimeStatus,
        compute_all_account_runtime_statuses,
    )
    from core_engine.services.rubika_l17_automation import (
        account_is_canonical_managed,
        account_is_legacy_protected,
    )

    db = SessionLocal()
    baselines = {}
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        accounts = db.query(Account).order_by(Account.id.asc()).all()
        runtimes = compute_all_account_runtime_statuses(db, accounts)
        by_id = {r.account_id: r for r in runtimes}

        # Old UI would show Account.status as the primary badge ("فعال" for active).
        inventory = []
        mismatches = []
        grouped = defaultdict(list)
        unexplained = []

        for a in accounts:
            r = by_id[int(a.id)]
            old_ui = a.status.value if hasattr(a.status, "value") else str(a.status)
            old_ui_fa = {
                "active": "فعال",
                "resting": "استراحت",
                "banned": "مسدود",
                "requires_login": "نیاز به ورود",
            }.get(old_ui, old_ui)
            expected_fa = r.runtime_status_label
            mismatch = old_ui_fa != expected_fa and not (
                old_ui == "requires_login" and r.runtime_status == RuntimeStatus.LOGIN_REQUIRED.value
            )
            # Primary L18 mismatch: showing فعال while not READY/connected-equivalent
            if old_ui == "active" and r.runtime_status not in {
                RuntimeStatus.READY.value,
                RuntimeStatus.AUTHENTICATED_NO_WORKER.value,
            }:
                mismatch = True
                mismatch_kind = "UI_ACTIVE_IMPLIES_CONNECTED_LIE"
            elif old_ui == "active" and r.runtime_status == RuntimeStatus.READY.value:
                mismatch = False
                mismatch_kind = None
            elif mismatch:
                mismatch_kind = "LIFECYCLE_VS_RUNTIME_LABEL"
            else:
                mismatch_kind = None

            if r.runtime_status not in {s.value for s in RuntimeStatus}:
                unexplained.append(int(a.id))

            row = {
                "account_id": int(a.id),
                "platform": a.platform.value,
                "enabled": r.enabled,
                "auth_state": r.auth_state,
                "credential_session_state": r.credential_state,
                "identity_state": r.identity_state,
                "worker_state": r.worker_state,
                "worker_covered": r.worker_covered,
                "dispatch_ready": r.dispatch_ready,
                "normalized_runtime_status": r.runtime_status,
                "reason_code": r.reason_code,
                "frontend_current_status_pre_l18": old_ui_fa,
                "frontend_expected_status": expected_fa,
                "mismatch": mismatch,
                "mismatch_kind": mismatch_kind,
                "operator_action_required": r.operator_action_code,
                "operator_action_label": r.operator_action_label,
                "canonical_managed": account_is_canonical_managed(db, a.id)
                if a.platform.value == "rubika"
                else False,
                "legacy_protected": account_is_legacy_protected(db, a.id)
                if a.platform.value == "rubika"
                else False,
            }
            inventory.append(row)
            grouped[r.runtime_status].append(int(a.id))
            if mismatch:
                mismatches.append(row)

        msg_count = db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0
        challenge_count = db.query(RubikaLoginChallenge).count()
        active_sessions = (
            db.query(ChannelSession)
            .filter(ChannelSession.session_status == "active")
            .count()
        )

        art = {
            "READ_ONLY": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "TOTAL_ACCOUNTS": len(accounts),
            "CLASSIFIED_ACCOUNTS": len(accounts) - len(unexplained),
            "UNEXPLAINED_ACCOUNTS": len(unexplained),
            "unexplained_ids": unexplained,
            "grouped": {k: v for k, v in grouped.items()},
            "inventory": inventory,
            "mismatch_count_pre_deploy": len(mismatches),
            "baselines": {
                "message_attempts": int(msg_count),
                "login_challenges": int(challenge_count),
                "active_sessions": int(active_sessions),
            },
            "PRODUCTION_READ_ONLY_AUDIT_PASS": len(unexplained) == 0 and len(accounts) >= 1,
        }
        baselines.update(art["baselines"])
    finally:
        db.rollback()
        db.close()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    INV.write_text(json.dumps(art, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MATRIX.write_text(
        json.dumps(
            {
                "READ_ONLY": True,
                "pre_deploy_mismatches": mismatches,
                "count": len(mismatches),
                "note": (
                    "Pre-L18 UI showed Account.status (فعال) as primary. "
                    "Post-deploy UI must show runtime_status_label instead."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# L18 Final Account Truth Audit",
        "",
        f"Generated: {art['generated_at']}",
        "",
        "## Summary",
        f"- TOTAL_ACCOUNTS={art['TOTAL_ACCOUNTS']}",
        f"- CLASSIFIED_ACCOUNTS={art['CLASSIFIED_ACCOUNTS']}",
        f"- UNEXPLAINED_ACCOUNTS={art['UNEXPLAINED_ACCOUNTS']}",
        f"- Pre-deploy UI/backend label mismatches={art['mismatch_count_pre_deploy']}",
        "",
        "## Grouped runtime statuses",
    ]
    for k, ids in sorted(art["grouped"].items()):
        lines.append(f"- **{k}** ({len(ids)}): {ids}")
    lines.extend(
        [
            "",
            "## Data path (forensic)",
            "",
            "DB Account.status / ChannelSession / LoginChallenge / Redis coverage",
            "→ `account_runtime_status.compute_*`",
            "→ `GET /accounts` (`runtime`, `runtime_status`, `runtime_status_label`)",
            "→ Accounts page connection column (not lifecycle `فعال` alone)",
            "",
            "Legacy lie fixed: `status=active` is **وضعیت اکانت**, not **وضعیت اتصال**.",
            "",
        ]
    )
    AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "TOTAL_ACCOUNTS": art["TOTAL_ACCOUNTS"],
        "CLASSIFIED_ACCOUNTS": art["CLASSIFIED_ACCOUNTS"],
        "UNEXPLAINED_ACCOUNTS": art["UNEXPLAINED_ACCOUNTS"],
        "mismatch_count_pre_deploy": art["mismatch_count_pre_deploy"],
        "grouped_counts": {k: len(v) for k, v in art["grouped"].items()},
        "PASS": art["PRODUCTION_READ_ONLY_AUDIT_PASS"],
        "INV": str(INV),
    }, ensure_ascii=False, indent=2))
    return 0 if art["PRODUCTION_READ_ONLY_AUDIT_PASS"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
