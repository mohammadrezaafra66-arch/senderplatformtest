# R10 Forensic Cleanup Verification

**Status:** COMMITTED — forensic data cleanup complete  
**Executed:** 2026-08-29T09:04:50Z  
**Mode:** one transaction; exact KEEP restores + exact DELETE_IDS  
**Worker:** `mmp_rubika_worker` remained **Exited** (not started)  
**R10 real send:** **not executed** (blocked pending fresh operator approval)

## Plan / script anchors

| Anchor | Value |
|--------|-------|
| Plan | `R10_CLEANUP_PLAN_DATA.json` |
| Plan SHA256 | `31d62e4873e6406bfb75ce3da98ffd0f47d5b563783185dc03e0c4841739e02e` |
| DELETE_ROW_COUNT_TOTAL | 2166 |
| DELETE_ROW_COUNT_ACTUAL | 2166 |
| Script | `scripts/_r10_execute_cleanup.py` (exact-scope KEEP + verifier asyncio fix) |

## KEEP restores applied

| table | id | column | from | to |
|-------|-----|--------|------|-----|
| campaign_recipients | 4 | final_message_id | 201 | NULL |
| campaign_recipients | 4 | send_status | DELIVERED | PENDING |
| contacts | 92 | campaign_id | 40 | NULL |

## Post-cleanup counts (fresh read)

| Metric | Expected | Actual |
|--------|----------|--------|
| accounts | 48 | 48 |
| Rubika accounts | 43 | 43 |
| campaigns | 4 | 4 |
| contacts | 3 | 3 |
| messages | 1 | 1 |
| test debris (`r22-%`/`p6-%` + accounts id>92) | 0 | 0 |

## Preservation

| Entity | Status |
|--------|--------|
| campaigns 1,2,3,4 | present |
| contacts 1,2,92 | present; campaign_id NULL on all three |
| campaign_recipients.id=4 | present; `final_message_id=NULL`, `send_status=PENDING` |
| message 1 | present |
| accounts 12,79,92 | present |
| sessions 11,12 (acct 92); 600 (79); 657,728 (12) | present |
| schedule 195 | active, phase=`day` |
| pool 12 / 79 / 92 | 1 / 1 / 2 |

## Readiness / queues / safety

| Check | Value |
|-------|--------|
| Account12 | READY |
| Account79 | READY |
| queue:rubika:12 | 0 |
| queue:rubika:79 | 0 |
| FK_INTEGRITY_PASS | True |
| PRODUCTION_PRESERVATION_PASS | True |
| MESSAGE_SENT | False |
| OTP_REQUESTED | False |
| RUBIKA_WORKER_STARTED | False |

## Result

```
R10_FORENSIC_CLEANUP_PASS=True
CURRENT_PHASE=R10_CONTROLLED_REAL_SEND
PHASE_STATUS=BLOCKED
BLOCK_REASON=Cleanup complete; controlled real-send requires fresh operator approval
SAFE_STATE_CONFIRMED=True
```

Artifact: `reports/rubika-remediation/R10_CLEANUP_AFTER.json`
