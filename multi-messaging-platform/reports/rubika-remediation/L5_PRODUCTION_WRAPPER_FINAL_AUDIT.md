# L5 — FINAL SOURCE AUDIT: `_l5_production_schema_apply.py`

**Date:** 2026-08-31  
**Mode:** SOURCE AUDIT ONLY — no execution, no Alembic, no production mutation  
**Verdict:** Wrapper is **not yet safe** for production L2 stage without hardening gaps listed below.

---

## Gate-by-gate

### 1. Positive authorization — PASS

```47:51:scripts/_l5_production_schema_apply.py
def assert_production_migration_authorized() -> None:
    if os.environ.get("ALLOW_PRODUCTION_SCHEMA_MIGRATION") != "1":
        raise RuntimeError(
            "REFUSE: set ALLOW_PRODUCTION_SCHEMA_MIGRATION=1 for production schema apply"
        )
```

- Exact value `"1"` required; unset/empty/other → refuse  
- Called on every non-help step and before `_alembic`  
- No default allow  

`POSITIVE_AUTHORIZATION_REQUIRED=True`

---

### 2. Backup gate — PARTIAL FAIL

Present:

- Requires `FRESH_BACKUP_PATH` + `FRESH_BACKUP_SHA256`
- File must exist
- SHA256 must match exactly

Missing:

| Requirement | Present? |
|---|---|
| File non-empty | **No** (empty file still hashes) |
| Backup timestamp from this migration attempt | **No** |
| Backup corresponds to `mmp_db` | **No** (no dump/meta provenance check) |

`BACKUP_HASH_GATE_ENFORCED=False` (hash match yes; full gate incomplete)

---

### 3. Target database lock — PASS

- Fixed `PRODUCTION_DB = "mmp_db"`
- URL built from trusted constants + fixed DB name (does not inherit env `DATABASE_URL` for path)
- Prints `event=alembic_production_target db=...` before mutation
- `assert_production_db_name` refuses any other name

`TARGET_DB_FIXED_TO_MMP_DB=True`

---

### 4. Precheck gates — FAIL (incomplete + not blocking on L2)

`precheck()` enforces only:

- `alembic_version=campaign_accounts_001`
- Rubika accounts = 43
- Account12 sessions = 657, 728
- Account79 session = 600
- `session_status` column absent

**Not implemented in wrapper:**

- Campaign101=paused / Campaign102=paused
- queue12=0 / queue79=0
- `RUBIKA_CANONICAL_SESSION_V1=false` on production services
- `RUBIKA_ACCOUNT_IDS` contains 12,79
- no running remediation campaigns

**Critical control-flow gap:** `L5_APPLY_STEP=l2` does **not** call `precheck()` before Alembic:

```288:290:scripts/_l5_production_schema_apply.py
    elif step == "l2":
        _alembic("upgrade", REVISION_L2)
        artifact["verify_l2"] = verify_l2()
```

Operator must manually run `precheck` first; the wrapper does not hard-block L2 on failed/missing precheck.

`PRECHECKS_HARD_BLOCKING=False`

---

### 5. Source hash lock — FAIL (absent)

No computation or comparison of SHA256 for:

- `rubika_l2_canonical_session_001.py`
- `rubika_l3_login_challenge_001.py`
- `scripts/_l5_production_schema_apply.py`

against `L5_PRODUCTION_SCHEMA_APPLY_PLAN.json` → `migration_source_hashes`.

`SOURCE_HASH_LOCK_ENFORCED=False`

---

### 6. Exact revision execution — PASS

- Uses `REVISION_L2` / `REVISION_L3` constants only
- No `alembic upgrade head`
- L2: `upgrade rubika_l2_canonical_session_001`
- L3: `upgrade rubika_l3_login_challenge_001`

`L2_EXPLICIT_REVISION_ONLY=True`  
`L3_EXPLICIT_REVISION_ONLY=True`

---

### 7. L2 intermediate stop gate — PARTIAL FAIL

Good:

- `L5_APPLY_STEP=l3` calls `verify_l2()` before L3 Alembic
- Asserts `alembic_version=rubika_l2_canonical_session_001`
- Asserts ACTIVE count = 0
- Asserts all rows `legacy_unclassified` (`legacy_count == total_sessions`)

Gaps in `verify_l2()`:

| Assertion | Queried? | Asserted? |
|---|---|---|
| Account12 = 657,728 | Yes | **No** |
| Account79 = 600 | Yes | **No** |
| Session count unchanged vs precheck baseline | Has total only | **No baseline** |
| Duplicates preserved | No | **No** |
| Structured rollback recommendation | No | **No** |

`L2_INTERMEDIATE_STOP_ENFORCED=False` (stop exists; required assertions incomplete)

---

### 8. L3 postcheck — PARTIAL FAIL

Present: version = L3, ACTIVE=0, challenge_count=0  

Missing:

- enum exists exactly once
- all sessions still `legacy_unclassified` (`legacy_count` queried but **not** asserted)
- Account12/79 mappings unchanged

---

### 9. Feature flags — PASS (no production flag mutation)

- Runner env forces `RUBIKA_CANONICAL_SESSION_V1=false` for one-shot container only
- Does not enable canonical flag
- Does not mutate compose/`RUBIKA_ACCOUNT_IDS` / `AUTO_ENROLL_RUBIKA_POOL`

`FEATURE_FLAG_MUTATION_PRESENT=False`

---

### 10. External effects — PASS

No OTP request/submit, Rubika network, campaign start, enqueue, send, or worker/API restart in wrapper.

`WORKER_RESTART_PRESENT=False`  
`OTP_OR_SEND_PATH_PRESENT=False`

---

### 11. Failure behavior — PARTIAL PASS

Present:

- Exceptions stop immediately
- No auto-continue to next revision
- No automatic `pg_restore` / destructive rollback

Missing:

- Structured “failed gate” taxonomy
- Explicit recommendation: no-op / L3 downgrade / L2 downgrade / full restore

`AUTO_ROLLBACK_PRESENT=False`

---

## Blocking gaps before SAFE_TO_RUN_PRODUCTION_L2_STAGE

1. Auto-invoke full precheck before `l2` (hard refuse if fail)
2. Add missing prechecks: campaigns 101/102 paused, queues 0, flag false, account IDs pin, no active remediation campaigns
3. Source hash lock vs L5 plan
4. Backup: non-empty + timestamp freshness + mmp_db provenance
5. `verify_l2` must assert Account12/79 IDs, session count baseline, duplicates
6. Failure path must print rollback recommendation (no auto-restore)

---

## Final flags

```
POSITIVE_AUTHORIZATION_REQUIRED=True
BACKUP_HASH_GATE_ENFORCED=False
TARGET_DB_FIXED_TO_MMP_DB=True

PRECHECKS_HARD_BLOCKING=False
SOURCE_HASH_LOCK_ENFORCED=False

L2_EXPLICIT_REVISION_ONLY=True
L2_INTERMEDIATE_STOP_ENFORCED=False
L3_EXPLICIT_REVISION_ONLY=True

FEATURE_FLAG_MUTATION_PRESENT=False
WORKER_RESTART_PRESENT=False
OTP_OR_SEND_PATH_PRESENT=False

AUTO_ROLLBACK_PRESENT=False

SAFE_TO_RUN_PRODUCTION_L2_STAGE=False

PRODUCTION_APPLY_EXECUTED=False

CURRENT_PHASE=L5_PRODUCTION_SCHEMA_APPLY_FINAL_AUDIT
PHASE_STATUS=BLOCKED
EXACT_OPERATOR_INPUT_REQUIRED=Approve production L2 schema stage only after final wrapper audit
```

**Next safe action:** Harden `_l5_production_schema_apply.py` for the gaps above, then re-audit. Do not run production migration.
