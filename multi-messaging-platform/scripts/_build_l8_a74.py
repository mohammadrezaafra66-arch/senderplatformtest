"""Adapt hardened Account23 production runner -> Account74 / Session724.

SOURCE-ONLY builder:
  read:  scripts/_l8_promote_account23_production.py
  write: scripts/_l8_promote_account74_production.py

No Docker, DB, Redis, pytest, Alembic, subprocess, or promotion execution.
"""

from __future__ import annotations

from pathlib import Path

SRC_NAME = "_l8_promote_account23_production.py"
DST_NAME = "_l8_promote_account74_production.py"

# Specific tokens BEFORE broader tokens that would rewrite them.
# Example: L8_QUEUE23_LEN must precede QUEUE23 (else L8_QUEUE23_LEN vanishes).
REPLACEMENTS: list[tuple[str, str]] = [
    # --- most specific env / queue tokens first ---
    ("L8_QUEUE23_LEN", "L8_QUEUE74_LEN"),
    ("queue:rubika:23", "queue:rubika:74"),
    ("QUEUE23", "QUEUE74"),
    ("queue23", "queue74"),
    # --- auth / phase / artifact names ---
    ("L8_PRODUCTION_TARGET=account23", "L8_PRODUCTION_TARGET=account74"),
    ('if target != "account23":', 'if target != "account74":'),
    ('expected="account23",', 'expected="account74",'),
    ("L8_PRODUCTION_ACCOUNT23", "L8_PRODUCTION_ACCOUNT74"),
    ("L8_BATCH_A_ACCOUNT23_PROMOTION", "L8_BATCH_A_ACCOUNT74_PROMOTION"),
    ("L8_ACCOUNT23_LAST_FAILURE.json", "L8_ACCOUNT74_LAST_FAILURE.json"),
    (
        'OUT = REPORT_DIR / "L8_ACCOUNT23_PROMOTION.json"',
        'OUT = REPORT_DIR / "L8_ACCOUNT74_PROMOTION.json"',
    ),
    ("mmp_db_pre_account23_", "mmp_db_pre_account74_"),
    (
        "scripts/_l8_promote_account23_production.py",
        "scripts/_l8_promote_account74_production.py",
    ),
    # --- promotion result flag names (specific ACCOUNT23_* before any broader) ---
    ("ACCOUNT23_PROMOTION_PASS", "ACCOUNT74_PROMOTION_PASS"),
    ("ACCOUNT23_ACTIVE_SESSION_ID", "ACCOUNT74_ACTIVE_SESSION_ID"),
    ("ACCOUNT23_CANONICAL_LOADER_PASS", "ACCOUNT74_CANONICAL_LOADER_PASS"),
    ("ACCOUNT23_LEGACY_CANONICAL_MATCH", "ACCOUNT74_LEGACY_CANONICAL_MATCH"),
    # --- header / constants / operator notes ---
    (
        "L8 production promotion — Account23 / Session725 ONLY (hardened).",
        "L8 production promotion — Account74 / Session724 ONLY (hardened).",
    ),
    (
        "Never promotes 74/2/12/79/etc. Do not modify Account13.",
        "Never promotes 2/12/79/etc. Do not modify Account13 or Account23.",
    ),
    ("ACCOUNT_ID = 23\nSESSION_ID = 725", "ACCOUNT_ID = 74\nSESSION_ID = 724"),
    ("operator-approved Account23 only", "operator-approved Account74 only"),
    ("mutate Account23/725 only", "mutate Account74/724 only"),
    ("Drop session 725", "Drop session 724"),
    (
        "Review Account23 before approving Account74 promotion",
        "Review Account74 before next Batch A step",
    ),
]


def _apply_replacements(text: str) -> str:
    for src, dst in REPLACEMENTS:
        if src not in text:
            raise SystemExit(f"PREWRITE_FAIL: missing source token {src!r}")
        text = text.replace(src, dst)
    return text


def _apply_precheck_and_postcheck_blocks(t: str) -> str:
    # Multi-line blocks that still contain id=725 / account 23 MUST be rewritten
    # before any broad "WHERE id=725" / similar token replacement.

    # verify restore helper rename + body (still Account23/725 in source)
    if "verify_account23_restored_to_prepromotion" not in t:
        raise SystemExit("PREWRITE_FAIL: verify_account23_restored_to_prepromotion missing")
    t = t.replace(
        "verify_account23_restored_to_prepromotion",
        "verify_account74_restored_to_prepromotion",
    )
    t = t.replace(
        "Host-side check that Session725 / Account23 match pre-promotion invariants.",
        "Host-side check that Session724 / Account74 match pre-promotion invariants.",
    )

    old_verify = '''    a23 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=23 ORDER BY id;"
    )
    mapping = _psql(
        "SELECT account_id||':'||session_status::text FROM channel_sessions WHERE id=725;"
    )
    guid = _psql("SELECT COALESCE(rubika_guid, '') FROM accounts WHERE id=23;")
    status = _psql(
        "SELECT COALESCE(rubika_identity_status::text, '') FROM accounts WHERE id=23;"
    )
    ok = True
    detail: dict[str, Any] = {
        "a23": a23,
        "mapping": mapping,
        "rubika_guid": guid,
        "rubika_identity_status": status,
    }
    if a23 != before.get("a23"):
        ok = False
        detail["a23_mismatch"] = {"expected": before.get("a23"), "actual": a23}
    if mapping != "23:legacy_unclassified":
        ok = False
        detail["mapping_mismatch"] = {
            "expected": "23:legacy_unclassified",
            "actual": mapping,
        }'''

    new_verify = '''    a74 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=74 ORDER BY id;"
    )
    mapping = _psql(
        "SELECT account_id||':'||session_status::text FROM channel_sessions WHERE id=724;"
    )
    guid = _psql("SELECT COALESCE(rubika_guid, '') FROM accounts WHERE id=74;")
    status = _psql(
        "SELECT COALESCE(rubika_identity_status::text, '') FROM accounts WHERE id=74;"
    )
    ok = True
    detail: dict[str, Any] = {
        "a74": a74,
        "mapping": mapping,
        "rubika_guid": guid,
        "rubika_identity_status": status,
    }
    if a74 != before.get("a74"):
        ok = False
        detail["a74_mismatch"] = {"expected": before.get("a74"), "actual": a74}
    if mapping != "74:legacy_unclassified":
        ok = False
        detail["mapping_mismatch"] = {
            "expected": "74:legacy_unclassified",
            "actual": mapping,
        }'''
    if old_verify not in t:
        raise SystemExit("PREWRITE_FAIL: verify body block missing")
    t = t.replace(old_verify, new_verify)

    old_global = '''    # Account13 must remain ACTIVE Session729; global ACTIVE == 1
    a13 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=13 ORDER BY id;"
    )
    if a13 != "729:active":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT13_REMAINS_ACTIVE_729",
            expected="729:active",
            actual=a13,
        )
    active_global = _psql(
        "SELECT COUNT(*) FROM channel_sessions WHERE session_status::text='active';"
    )
    if active_global != "1":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="GLOBAL_ACTIVE_SESSION_COUNT",
            expected="1",
            actual=active_global,
        )
    a74 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=74 ORDER BY id;"
    )
    if "active" in (a74 or ""):
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT74_NOT_ACTIVE",
            expected="legacy_unclassified only",
            actual=a74,
        )'''

    new_global = '''    # Account13 ACTIVE 729 + Account23 ACTIVE 725; global ACTIVE == 2
    a13 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=13 ORDER BY id;"
    )
    if a13 != "729:active":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT13_REMAINS_ACTIVE_729",
            expected="729:active",
            actual=a13,
        )
    a23 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=23 ORDER BY id;"
    )
    if a23 != "725:active":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT23_REMAINS_ACTIVE_725",
            expected="725:active",
            actual=a23,
        )
    active_global = _psql(
        "SELECT COUNT(*) FROM channel_sessions WHERE session_status::text='active';"
    )
    if active_global != "2":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="GLOBAL_ACTIVE_SESSION_COUNT",
            expected="2",
            actual=active_global,
        )'''

    if old_global not in t:
        raise SystemExit("PREWRITE_FAIL: old_global precheck block missing")
    t = t.replace(old_global, new_global)

    old_return = '''    return {
        "mode": mode,
        "queue74": q,
        "mapping": mapping,
        "a13": a13,
        "active_global": int(active_global),
        "a74": a74,
    }'''
    new_return = '''    return {
        "mode": mode,
        "queue74": q,
        "mapping": mapping,
        "a13": a13,
        "a23": a23,
        "active_global": int(active_global),
    }'''
    if old_return not in t:
        raise SystemExit("PREWRITE_FAIL: precheck return block missing")
    t = t.replace(old_return, new_return)

    # Remaining host_precheck SQL/gate tokens (after multi-line 725 blocks rewritten)
    for a, b in [
        ("accounts WHERE id=23;", "accounts WHERE id=74;"),
        ("ACCOUNT23_EXISTS", "ACCOUNT74_EXISTS"),
        ("WHERE id=725;", "WHERE id=724;"),
        ('expected="23:legacy_unclassified"', 'expected="74:legacy_unclassified"'),
        ("SESSION725_MAPPING_STATUS", "SESSION724_MAPPING_STATUS"),
        (
            '"SELECT COUNT(*) FROM channel_sessions WHERE account_id=23 "\n'
            '        "AND session_status::text=\'active\';"',
            '"SELECT COUNT(*) FROM channel_sessions WHERE account_id=74 "\n'
            '        "AND session_status::text=\'active\';"',
        ),
        ("ACCOUNT23_ACTIVE_ZERO", "ACCOUNT74_ACTIVE_ZERO"),
        (
            "WHERE ca.account_id=23 AND c.status::text='running';",
            "WHERE ca.account_id=74 AND c.status::text='running';",
        ),
        (
            'if mapping != "23:legacy_unclassified":',
            'if mapping != "74:legacy_unclassified":',
        ),
    ]:
        if a not in t:
            raise SystemExit(f"PREWRITE_FAIL: missing precheck piece {a!r}")
        t = t.replace(a, b)

    for a, b in [
        (
            '"MANUAL_INTERVENTION_REQUIRED: host postcheck failed after Account23 "\n'
            '            "promotion commit and exact rollback did not restore pre-promotion state. "\n'
            '            "Do not auto full-DB restore; intervene for Account23/Session725 only."',
            '"MANUAL_INTERVENTION_REQUIRED: host postcheck failed after Account74 "\n'
            '            "promotion commit and exact rollback did not restore pre-promotion state. "\n'
            '            "Do not auto full-DB restore; intervene for Account74/Session724 only."',
        ),
        (
            "Call reviewed rollback_legacy_promotion for Account23/725 only (core_api).",
            "Call reviewed rollback_legacy_promotion for Account74/724 only (core_api).",
        ),
        (
            "Worker role: exact-ID rollback for Account23 / Session725 only.",
            "Worker role: exact-ID rollback for Account74 / Session724 only.",
        ),
        (
            "If promotion committed and host postcheck fails, invoke exact 23/725 rollback.",
            "If promotion committed and host postcheck fails, invoke exact 74/724 rollback.",
        ),
        (
            '_assert_eq(stage, "GLOBAL_ACTIVE_SESSION_COUNT", 2, after["active_global"])',
            '_assert_eq(stage, "GLOBAL_ACTIVE_SESSION_COUNT", 3, after["active_global"])',
        ),
        (
            'if f"{SESSION_ID}:23:active" not in (after["session_inventory"] or ""):',
            'if f"{SESSION_ID}:74:active" not in (after["session_inventory"] or ""):',
        ),
        ("SESSION725_ACTIVE_IN_INVENTORY", "SESSION724_ACTIVE_IN_INVENTORY"),
        (
            'expected=f"{SESSION_ID}:23:active",',
            'expected=f"{SESSION_ID}:74:active",',
        ),
    ]:
        if a not in t:
            raise SystemExit(f"PREWRITE_FAIL: missing block token {a[:80]!r}...")
        t = t.replace(a, b)

    old_post_nt = '''    _assert_eq(stage, "ACCOUNT13_UNCHANGED", before["a13"], after["a13"])
    _assert_eq(stage, "ACCOUNT13_STILL_ACTIVE_729", "729:active", after["a13"])
    _assert_eq(stage, "ACCOUNT74_UNCHANGED", before["a74"], after["a74"])
    _assert_eq(stage, "ACCOUNT12_UNCHANGED", before["a12"], after["a12"])
    _assert_eq(stage, "ACCOUNT79_UNCHANGED", before["a79"], after["a79"])'''

    new_post_nt = '''    _assert_eq(stage, "ACCOUNT13_UNCHANGED", before["a13"], after["a13"])
    _assert_eq(stage, "ACCOUNT13_STILL_ACTIVE_729", "729:active", after["a13"])
    _assert_eq(stage, "ACCOUNT23_UNCHANGED", before["a23"], after["a23"])
    _assert_eq(stage, "ACCOUNT23_STILL_ACTIVE_725", "725:active", after["a23"])
    _assert_eq(stage, "ACCOUNT12_UNCHANGED", before["a12"], after["a12"])
    _assert_eq(stage, "ACCOUNT79_UNCHANGED", before["a79"], after["a79"])'''
    if old_post_nt not in t:
        raise SystemExit("PREWRITE_FAIL: post non-target block missing")
    t = t.replace(old_post_nt, new_post_nt)

    old_a13_inv = '''    if "729:13:active" not in (after["session_inventory"] or ""):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT13_SESSION729_STILL_ACTIVE",
            expected="729:13:active",
            actual=after["session_inventory"],
        )'''
    new_a13_inv = '''    if "729:13:active" not in (after["session_inventory"] or ""):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT13_SESSION729_STILL_ACTIVE",
            expected="729:13:active",
            actual=after["session_inventory"],
        )
    if "725:23:active" not in (after["session_inventory"] or ""):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT23_SESSION725_STILL_ACTIVE",
            expected="725:23:active",
            actual=after["session_inventory"],
        )'''
    if old_a13_inv not in t:
        raise SystemExit("PREWRITE_FAIL: Account13 inventory gate missing")
    t = t.replace(old_a13_inv, new_a13_inv)

    old_art = '''        "ACCOUNT13_UNCHANGED": True,
        "ACCOUNT74_UNCHANGED": True,
        "ACCOUNT12_UNCHANGED": True,
        "ACCOUNT79_UNCHANGED": True,'''
    new_art = '''        "ACCOUNT13_UNCHANGED": True,
        "ACCOUNT23_UNCHANGED": True,
        "ACCOUNT12_UNCHANGED": True,
        "ACCOUNT79_UNCHANGED": True,'''
    if old_art not in t:
        raise SystemExit("PREWRITE_FAIL: artifact unchanged flags missing")
    t = t.replace(old_art, new_art)

    # restore verifier default (may already be renamed by first replace)
    if "verify_account23_restored_to_prepromotion" in t:
        raise SystemExit("PREWRITE_FAIL: verify_account23 name still present")
    if "verify_account74_restored_to_prepromotion" not in t:
        raise SystemExit("PREWRITE_FAIL: verify_account74 name missing")

    return t


def _prewrite_validate(text: str) -> None:
    required = [
        "ACCOUNT_ID = 74",
        "SESSION_ID = 724",
        'L8_PRODUCTION_TARGET=account74',
        'if target != "account74":',
        "queue:rubika:74",
        "L8_QUEUE74_LEN",
        "ACCOUNT74_PROMOTION_PASS",
        "expected=\"2\"",  # precheck GLOBAL
        '_assert_eq(stage, "GLOBAL_ACTIVE_SESSION_COUNT", 3, after["active_global"])',
        "ACCOUNT13_REMAINS_ACTIVE_729",
        "ACCOUNT23_REMAINS_ACTIVE_725",
        "ACCOUNT23_STILL_ACTIVE_725",
        "invoke exact 74/724 rollback",
        "verify_account74_restored_to_prepromotion",
    ]
    for token in required:
        if token not in text:
            raise SystemExit(f"PREWRITE_FAIL: required marker missing after transform: {token!r}")

    prohibited_executable = [
        "ACCOUNT_ID = 23",
        "SESSION_ID = 725",
        'L8_PRODUCTION_TARGET=account23',
        'if target != "account23":',
        "queue:rubika:23",
        "L8_QUEUE23_LEN",
        "ACCOUNT23_PROMOTION_PASS",
        "scripts/_l8_promote_account23_production.py",
        "mmp_db_pre_account23_",
    ]
    for token in prohibited_executable:
        if token in text:
            raise SystemExit(f"PREWRITE_FAIL: prohibited executable marker remains: {token!r}")


def _postwrite_validate(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    checks = {
        "ACCOUNT_ID = 74": True,
        "SESSION_ID = 724": True,
        "L8_PRODUCTION_TARGET=account74": True,
        "ACCOUNT13_REMAINS_ACTIVE_729": True,
        "ACCOUNT23_REMAINS_ACTIVE_725": True,
        "ACCOUNT23_STILL_ACTIVE_725": True,
        "725:23:active": True,
        "729:13:active": True,
        "queue:rubika:74": True,
        'expected="2"': True,
        '"GLOBAL_ACTIVE_SESSION_COUNT", 3': True,
        "74/724": True,
        "ACCOUNT_ID = 23": False,
        "SESSION_ID = 725": False,
        "L8_PRODUCTION_TARGET=account23": False,
        "queue:rubika:23": False,
        "ACCOUNT23_PROMOTION_PASS": False,
    }
    for token, must_present in checks.items():
        present = token in text
        if must_present and not present:
            raise SystemExit(f"POSTWRITE_FAIL: missing required {token!r}")
        if not must_present and present:
            raise SystemExit(f"POSTWRITE_FAIL: prohibited executable marker {token!r}")

    # Harmless historical/protection leftovers — report only
    reported: list[str] = []
    for harmless in (
        "Account23",
        "account_id=23",
        "725:active",
        "Do not modify Account13 or Account23",
    ):
        if harmless in text:
            reported.append(harmless)
    print("POSTWRITE_OK", path)
    print("ALLOWED_HARMLESS_STRINGS:", ", ".join(reported) if reported else "(none)")


def main() -> int:
    here = Path(__file__).resolve().parent
    src = here / SRC_NAME
    dst = here / DST_NAME
    if not src.is_file():
        raise SystemExit(f"missing source {src}")

    raw = src.read_text(encoding="utf-8")
    t = _apply_replacements(raw)
    t = _apply_precheck_and_postcheck_blocks(t)
    _prewrite_validate(t)
    dst.write_text(t, encoding="utf-8")
    _postwrite_validate(dst)
    print("wrote", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
