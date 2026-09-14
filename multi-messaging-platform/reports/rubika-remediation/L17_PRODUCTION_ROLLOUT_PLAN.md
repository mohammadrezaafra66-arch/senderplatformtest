# L17 — Production Automation Cleanup Rollout Plan

**DO NOT EXECUTE until operator approves.**

## Rollback target (current proven production)

```
RUBIKA_CANONICAL_SESSION_MODE=enforce
RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=13,23,27,74
RUBIKA_CANONICAL_SESSION_SCOPE=allowlist   # implicit default today

RUBIKA_WORKER_DISCOVERY_MODE=dynamic
RUBIKA_WORKER_DISCOVERY_COHORT_IDS=13,23,27,74
RUBIKA_WORKER_DISCOVERY_SCOPE=cohort       # implicit default today
RUBIKA_ACCOUNT_IDS=12,79

RUBIKA_L3_LOGIN_ROUTING=pilot
RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS=27
AUTO_ENROLL_RUBIKA_POOL=false
```

Session772 and all ACTIVE canonical rows must remain untouched by any rollback.

## Proposed cleanup (single staged change)

Apply to `core_api` + `rubika_worker` (and celery env mirror if present):

```
RUBIKA_L3_LOGIN_ROUTING=auto_evidence
# keep pilot=27 as emergency override (optional; may clear later)

AUTO_ENROLL_RUBIKA_POOL=true

RUBIKA_WORKER_DISCOVERY_MODE=dynamic
RUBIKA_WORKER_DISCOVERY_SCOPE=all_eligible
RUBIKA_WORKER_DISCOVERY_COHORT_IDS=        # empty; ignored under all_eligible
RUBIKA_ACCOUNT_IDS=12,79                  # KEEP

RUBIKA_CANONICAL_SESSION_MODE=enforce
RUBIKA_CANONICAL_SESSION_SCOPE=canonical_active
# allowlist may remain as unused under canonical_active, or cleared after soak
RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=13,23,27,74
```

## Consumers to recreate

- `core_api`
- `rubika_worker`

No postgres/redis/frontend rebuild required (bind-mounted code already present; recreate for env uptake).

## Post-deploy verify

1. Worker IDs still include pin 12,79 and Batch A + 27  
2. Enforce applies to 13,23,27,74 only (simulation already predicts this)  
3. Account12/79 not auto-enforced  
4. New zero-session account routes L3 without pilot list edit  
5. MessageAttempt unchanged; no OTP; no send

## Exact operator input required

`Approve one final production automation-cleanup deployment`
