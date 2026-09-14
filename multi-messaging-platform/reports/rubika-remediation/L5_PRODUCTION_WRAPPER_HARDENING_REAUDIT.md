# L5 — HARDENED WRAPPER RE-AUDIT (source only)

**Date:** 2026-08-31  
**File:** `scripts/_l5_production_schema_apply.py`  
**Plan refresh:** `L5_PRODUCTION_SCHEMA_APPLY_PLAN.json` → `migration_source_hash_refresh`  
**Execution:** NONE (no Alembic, no production mutation)

## Plan hash refresh (explicit)

| File | SHA256 |
|---|---|
| `rubika_l2_canonical_session_001.py` | `0d1b82fa…` (unchanged) |
| `rubika_l3_login_challenge_001.py` | `aed15577…` (unchanged) |
| `scripts/_l5_production_schema_apply.py` | `bd8c3d1aa8391e2720a8bf94164f787c2665f1ae4979f490318328dbbd6ae994` |

**Reason:** Wrapper hardening after final audit gaps; migration SQL unchanged.

## Gate coverage (source)

| Gate | Status |
|---|---|
| `ALLOW_PRODUCTION_SCHEMA_MIGRATION==1` | Required |
| Backup exists, size>0, SHA256 match | Required |
| Backup meta `source_db=mmp_db` + freshness ≤30m | Required |
| Source hashes vs L5 plan (L2+L3+wrapper) | Required |
| `apply_l2()` always calls `precheck()` before Alembic | Required |
| Precheck: version, 43 accounts, 12/79 maps, camps paused, queues 0, flags false, pin 12,79, no active remediation | Required |
| Baseline persisted for verify_l2 | Required |
| `verify_l2` hard-asserts maps/count/duplicates/ACTIVE=0 | Required |
| `l3` re-verifies L2 + runtime gates; explicit L3 only | Required |
| `GateFailure` structured rollback recommendation | Required |
| No auto-restore / no worker restart / no OTP/send | Required |

## Flow

```
authorize → backup verify → source hash lock → precheck → L2 apply → verify_l2 → STOP
```

L3 is a separate step only.

## Verdict

Source hardening complete. Operator approval still required before any production L2 execution.
