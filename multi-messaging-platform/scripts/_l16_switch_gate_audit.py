#!/usr/bin/env python3
"""Source audit for _l16_switch_pilot_gate.py — no production mutation."""
from __future__ import annotations

import json
import re
from pathlib import Path

src = Path("scripts/_l16_switch_pilot_gate.py").read_text(encoding="utf-8")

checks = {
    "SWITCH_GATE_SOURCE_AUDIT_PASS": False,
    "SWITCH_GATE_TARGET_LOCKED_TO_27": "SWITCH_GATE_TARGET_LOCKED_TO_27" in src
    and 'if pilot != 27' in src,
    "CONFIG_MUTATION_SCOPE_EXACT": (
        "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS" in src
        and 'RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS: "13,23,74"' in src
        and 'RUBIKA_WORKER_DISCOVERY_COHORT_IDS: "13,23,74"' in src
        and 'RUBIKA_ACCOUNT_IDS: "12,79"' in src
    ),
    "CORE_API_ONLY_RECREATE": (
        "--no-deps" in src
        and '"core_api"' in src
        and "rubika_worker" not in src.split("def recreate_core_api")[1].split("def main")[0]
    ),
    "FRESH_DB_BACKUP_SAFE": "pg_dump" in src and "source_db" in src and "sha_match" in src,
    "SCRIPT_REQUESTS_OTP": bool(re.search(r"request_rubika_login\s*\(", src)),
    "SCRIPT_CREATES_LOGIN_CHALLENGE": "RubikaLoginChallenge" in src or "LoginChallenge" in src,
    "SCRIPT_CREATES_SESSION": "ChannelSession" in src or "store_channel_session" in src,
    "SCRIPT_SENDS_MESSAGES": "message_attempt" in src.lower() or "send_message" in src,
}
checks["SWITCH_GATE_SOURCE_AUDIT_PASS"] = all(
    [
        checks["SWITCH_GATE_TARGET_LOCKED_TO_27"],
        checks["CONFIG_MUTATION_SCOPE_EXACT"],
        checks["CORE_API_ONLY_RECREATE"],
        checks["FRESH_DB_BACKUP_SAFE"],
        not checks["SCRIPT_REQUESTS_OTP"],
        not checks["SCRIPT_CREATES_LOGIN_CHALLENGE"],
        not checks["SCRIPT_CREATES_SESSION"],
        not checks["SCRIPT_SENDS_MESSAGES"],
    ]
)
print(json.dumps(checks, indent=2))
raise SystemExit(0 if checks["SWITCH_GATE_SOURCE_AUDIT_PASS"] else 2)
