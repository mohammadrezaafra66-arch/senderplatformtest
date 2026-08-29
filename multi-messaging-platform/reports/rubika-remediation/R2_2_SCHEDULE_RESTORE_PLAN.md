# R2.2 Targeted Schedule Restore Plan (PRE-MUTATION)

**Captured:** 2026-08-29  
**Branch:** `remediation/rubika-multi-account-production-20260826_160629`  
**HEAD:** `56b84bb`  
**Evidence:** `R2_2_SCHEDULE_RESTORE_BEFORE.json`  
**File SHA256:** `6E065B81FE054B166C559E435901D46FB929D576656046D20081EDF000BDB2D3`  
**Body SHA256 (JSON body field):** `18ad8aa38dbb9542c0a8044d3f62cba8dd9d80ec436d9b781a6f302769632b61`

## Verified production day schedule

| Field | Value |
|-------|-------|
| id | **195** |
| phase | day |
| start_hour | 8 |
| end_hour | 22 |
| max_per_hour | **50** |
| is_active | **false** (needs activate) |
| created_at | 2026-08-24T10:24:36.535222 |

Matches historical expectation (id=195, 8–22, max_per_hour=50). Verified from live DB — not assumed.

## Pool membership controlling Accounts 12 and 79

| Account | pool_row_id | phase | priority |
|---------|-------------|-------|----------|
| 12 | 9 | **day** | 1 |
| 79 | 36 | **day** | 1 |

Neither account is in any active debris phase. Resolved phase today is `p6-pool-b0213b` → both fail `ACCOUNT_NOT_IN_ALLOWED_POOL`.

## Currently active rows (all confirmed debris)

| id | phase | window | max_per_hour |
|----|-------|--------|--------------|
| 287 | p6-pool-b0213b | 0–23 | 999 |
| 288 | p6-1e2fbb | 0–23 | 999 |

## Inactive confirmed debris (no change required; already inactive)

ids: 256, 257, 258, 259, 260, 261, 262, 263, 265, 266, 278, 279, 280, 281, 282, 283, 284, 285

## Targeted mutation plan (exact IDs only)

```
ACTIVATE:
id=195 phase=day

DEACTIVATE:
id=287 phase=p6-pool-b0213b
id=288 phase=p6-1e2fbb

EXPECTED_ACTIVE_PHASE_AFTER=day
EXPECTED_ACTIVE_SCHEDULE_IDS=[195]
EXPECTED_ACTIVE_DEBRIS_COUNT=0
```

SQL (single transaction, for approval):

```sql
BEGIN;
UPDATE rubika_sender_schedules SET is_active = true  WHERE id = 195 AND phase = 'day';
UPDATE rubika_sender_schedules SET is_active = false WHERE id = 287 AND phase = 'p6-pool-b0213b';
UPDATE rubika_sender_schedules SET is_active = false WHERE id = 288 AND phase = 'p6-1e2fbb';
-- verify before COMMIT (application-side checks); ROLLBACK on failure
COMMIT;
```

**No wildcard UPDATEs. No row deletes. No pool row changes.**

## Status

Awaiting operator approval before any DB write.
