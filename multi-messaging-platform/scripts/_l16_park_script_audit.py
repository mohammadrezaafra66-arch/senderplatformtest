#!/usr/bin/env python3
from pathlib import Path
import json
import re

src = Path("scripts/_l16_park_and_inventory.py").read_text(encoding="utf-8")
inv = src.split("async def inventory_no_session")[1].split("def recovery_plan")[0]
park = src.split("def park_account3")[1].split("def prove_other_account_login_allowed")[0]

# Executable OTP/login calls — ignore recovery_plan documentation strings.
exec_body = src.split("def recovery_plan")[0]
second_otp = bool(
    re.search(r"\brequest_rubika_login\s*\(", exec_body)
    or re.search(r"\bsubmit_rubika_login_code\s*\(", exec_body)
    or re.search(r"\bLiveRubikaLoginProvider\s*\(", exec_body)
    or "send_code(" in exec_body
)

challenge_delete = bool(
    re.search(r"db\.delete\s*\(", src)
    or "DELETE FROM rubika_login_challenges" in src
    or ".query(RubikaLoginChallenge).delete" in src
)

# Inventory Redis: only llen/ping/aclose — no writes
inv_redis_writes = bool(
    re.search(r"\br\.(set|setex|delete|lpush|rpush|lpop|rpop|hset)\s*\(", inv)
)

checks = {
    "PARK_SCRIPT_SOURCE_AUDIT_PASS": False,
    "ACCOUNT3_PARK_ACTION": "leave_challenge_intact_until_natural_expiry; clear_ephemeral_redis_handshake_only",
    "ACCOUNT3_CHALLENGE_DELETE_PRESENT": challenge_delete,
    "ACCOUNT3_SECOND_OTP_REQUEST_PRESENT": second_otp,
    "INVENTORY_DB_READ_ONLY": "SET TRANSACTION READ ONLY" in inv and "commit(" not in inv,
    "INVENTORY_REDIS_READ_ONLY": ("llen" in inv) and (not inv_redis_writes),
    "SCRIPT_SECRET_SAFE": (
        "_mask_phone" in src
        and "print(phone" not in src
        and "full_phone" not in src
    ),
}
checks["PARK_SCRIPT_SOURCE_AUDIT_PASS"] = all(
    [
        not checks["ACCOUNT3_CHALLENGE_DELETE_PRESENT"],
        not checks["ACCOUNT3_SECOND_OTP_REQUEST_PRESENT"],
        checks["INVENTORY_DB_READ_ONLY"],
        checks["INVENTORY_REDIS_READ_ONLY"],
        checks["SCRIPT_SECRET_SAFE"],
        "leave_intact_until_natural_expiry" in park,
    ]
)
print(json.dumps(checks, indent=2))
raise SystemExit(0 if checks["PARK_SCRIPT_SOURCE_AUDIT_PASS"] else 2)
