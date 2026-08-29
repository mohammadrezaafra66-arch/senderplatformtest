# R2.2 Schedule Restore Verification

**Date:** 2026-08-29  
**Result:** COMMIT  
**Transaction:** targeted IDs only (195 activate; 287/288 deactivate)

## Mutation executed

```
ACTIVATE:
id=195 phase=day

DEACTIVATE:
id=287 phase=p6-pool-b0213b
id=288 phase=p6-1e2fbb
```

No other `rubika_sender_schedules` rows modified. No deletes. No pool-row changes.

## Pre-commit verification (ALL PASS → COMMIT)

| # | Check | Result |
|---|-------|--------|
| 1 | active schedule IDs == [195] | PASS `[195]` |
| 2 | resolved phase == day | PASS `day` |
| 3 | Account12 in resolved pool | PASS |
| 4 | Account79 in resolved pool | PASS |
| 5 | Campaign 4 ready_accounts == 2 | PASS `2` |
| 6 | Campaign ↔ Transport parity | PASS (both READY for 12 and 79) |
| 7 | Account12 not ACCOUNT_NOT_IN_ALLOWED_POOL | PASS |
| 8 | Account79 not ACCOUNT_NOT_IN_ALLOWED_POOL | PASS |
| 9 | queue:rubika:12 unchanged | PASS `0→0` |
| 10 | queue:rubika:79 unchanged | PASS `0→0` |
| 11 | no message sent | PASS |
| 12 | no OTP requested | PASS |
| 13 | no message worker started | PASS |

Campaign 4 code remained `CAMPAIGN_NOT_PREPARED` (draft/unprepared — expected R1 semantics).

## Evidence

- Before: `R2_2_SCHEDULE_RESTORE_BEFORE.json`
- After: `R2_2_SCHEDULE_RESTORE_AFTER.json` (body SHA256 `df39dcc93fb0d72f0280b2f08e4a0bced2e8bafe369d07f55c34ec2de27b8fe3`)
- Plan: `R2_2_SCHEDULE_RESTORE_PLAN.md`

## R2 status

- R2.0 PASS (prior)
- R2.1 PASS (audit)
- R2.2 PASS (unified gate + schedule restore + runtime parity)

**R2_PASS=True**
