# L12 — Persistent Worker Discovery Shadow Deployment

**CURRENT_PHASE=L12_PERSISTENT_WORKER_DISCOVERY_SHADOW**  
**PHASE_STATUS=COMPLETE**  
**ROLLBACK_PERFORMED=False**  
**PRODUCTION_DYNAMIC_DISCOVERY_ENABLED=False**  
**PRODUCTION_ENFORCE_ENABLED=False**  
**WORKER_PIN_REMOVED=False**

## Config keys (implemented)

```
DISCOVERY_MODE_CONFIG_KEY=RUBIKA_WORKER_DISCOVERY_MODE
DISCOVERY_COHORT_CONFIG_KEY=RUBIKA_WORKER_DISCOVERY_COHORT_IDS
CONFIG_FILE=docker-compose.override.yml
```

## Provenance

```
ROLLBACK_FILE=docker-compose.override.yml.before-l12-discovery-shadow-20260831_133240.bak
ROLLBACK_SHA256=3c454008883051b16f14ffec7ba218efb642ba0a1f5f42997360a17a9d9507bf
PRE_CHANGE_CONFIG_SHA256=3c454008883051b16f14ffec7ba218efb642ba0a1f5f42997360a17a9d9507bf
POST_CHANGE_CONFIG_SHA256=f0147960be225525e8460c952d30f4dfe5528d97a9e75d0a489d4d938ba8ee84
```

Persisted on `rubika_worker` only:

- `RUBIKA_WORKER_DISCOVERY_MODE=shadow`
- `RUBIKA_WORKER_DISCOVERY_COHORT_IDS=` (empty)
- pin unchanged `RUBIKA_ACCOUNT_IDS=12,79`
- canonical unchanged `shadow` / `13,23,74`

## Worker image

```
RUBIKA_WORKER_REBUILD_REQUIRED=True
RUBIKA_WORKER_REBUILT=True
```

Initial recreate crash-looped on missing `MODE_DYNAMIC` import in `rubika_pool_worker.py`. Narrow import fix applied, image rebuilt, worker recreated successfully (no config rollback).

## Postdeploy

```
DISCOVERY_MODE=shadow
DISCOVERY_COHORT=
PINNED_SET=[12, 79]
DYNAMIC_ELIGIBLE_SET=[13, 23, 74, 79]
WOULD_ADD=[13, 23, 74]
WOULD_REMOVE=[12]
ACTUAL_WORKER_IDS=[12, 79]
coverage 12/79=True; 13/23/74=False
DISCOVERY_SHADOW_OBSERVATION_COUNT=3
DISCOVERY_CANDIDATE_SET_STABLE=True
GLOBAL_ACTIVE_SESSION_COUNT=3
MSG=5 LOGIN=0 unchanged
MESSAGE_SENT=False OTP_REQUESTED=False
```

Eligibility:

| account | eligible | note |
|---:|---|---|
| 12 | False | AMBIGUOUS_LEGACY_SESSIONS |
| 13 | True | |
| 23 | True | |
| 74 | True | |
| 79 | True | |

## Next canary (NOT executed)

```
RUBIKA_WORKER_DISCOVERY_MODE=dynamic
RUBIKA_WORKER_DISCOVERY_COHORT_IDS=13
RUBIKA_ACCOUNT_IDS=12,79
expected ACTUAL_WORKER_IDS=[12, 13, 79]
```

```
SAFE_TO_BEGIN_ACCOUNT13_DYNAMIC_WORKER_CANARY=True
EXACT_OPERATOR_INPUT_REQUIRED=Approve Account13-only dynamic worker canary while preserving pinned 12,79
```
