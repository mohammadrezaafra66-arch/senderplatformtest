# L9 — Batch A Canonical Shadow Verification

**CURRENT_PHASE=L9_BATCH_A_CANONICAL_SHADOW_VERIFICATION**  
**PHASE_STATUS=COMPLETE**  
**PRODUCTION_PERSISTENT_SHADOW_ENABLED=False**  
**PRODUCTION_ENFORCE_ENABLED=False**  
**WORKER_PIN_REMOVED=False**

## L9.1 Runtime provenance

| component | running_image | source_bind_mount | contains_canonical_runtime_modes | current_mode | current_allowlist | restart_required_for_persistent_config_change | rebuild_required_for_code_change |
|---|---|---|---|---|---|---|---|
| core_api | multi-messaging-platform-core_api | True (`core_engine`,`workers`,`scripts`) | True | unset→off | unset→empty | True | False (bind mount) |
| rubika_worker | multi-messaging-platform-rubika_worker | False | False (runtime module MISSING in image) | unset→off | n/a (no L7 code) | True | **True** |
| celery_worker | multi-messaging-platform-celery_worker | True (when running) | True (via bind mounts) | n/a (container **Exited** 5d) | n/a | True (if started) | False (bind mount) |

`workers/session_access.py` is the Rubika dispatch entry; it calls `load_rubika_runtime_session` when the file is present. That path is live on **core_api** (bind-mounted). The **rubika_worker** image still has a stale `session_access` without L7 wiring.

## L9.2 Current persistent config (secret-safe)

```
RUBIKA_CANONICAL_SESSION_MODE=(unset → off)
RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=(unset → empty)
RUBIKA_CANONICAL_SESSION_V1=(unset → false)
RUBIKA_ACCOUNT_IDS=12,79   # observed on mmp_rubika_worker only
```

Baseline: canonical off; global legacy compatibility preserved; worker pin still 12,79.

## L9.3 One-shot read-only shadow probe

Process-local only inside `mmp_core_api`:

`RUBIKA_CANONICAL_SESSION_MODE=shadow`  
`RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=13,23,74`

No core_api/rubika_worker restart. No compose/.env edit.

| account_id | legacy | canonical | shadow_result |
|---:|---:|---:|---|
| 13 | 729 | 729 | SHADOW_MATCH |
| 23 | 725 | 725 | SHADOW_MATCH |
| 74 | 724 | 724 | SHADOW_MATCH |

Runtime selection remained `source=legacy` under shadow (legacy-authoritative).

Artifact: `reports/rubika-remediation/L9_BATCH_A_SHADOW_RESULTS.json`

## L9.4 Mutation sentinels

```
TOTAL_CHANNEL_SESSION_COUNT: 15 → 15
GLOBAL_ACTIVE_SESSION_COUNT: 3 → 3
Account13: 729:active unchanged
Account23: 725:active unchanged
Account74: 724:active unchanged
MessageAttempt: 5 → 5
LoginChallenge: 0 → 0
queue:rubika:{13,23,74}: 0 → 0
session fingerprints unchanged
DB_MUTATION_DETECTED=False
REDIS_MUTATION_DETECTED=False
MESSAGE_SENT=False
OTP_REQUESTED=False
```

## L9.5 Authenticated reconnect

| account | session | reconnect | identity_match |
|---:|---:|---|---|
| 13 | 729 | PASS | True |
| 23 | 725 | PASS | True |
| 74 | 724 | PASS | True |

## L9.6 Shadow observability

Safe fields emitted: `account_id`, `legacy_selected_session_id`, `canonical_selected_session_id`, `canonical_error`, `match`, `metric`.

Metrics distinguished: `SHADOW_MATCH`, `SHADOW_NO_ACTIVE`, `SHADOW_DIFFERENT_SESSION`, `SHADOW_CANONICAL_ERROR`.

No ciphertext / tokens / phones / credentials in safe dicts or structured shadow logs.

## L9.7 Isolated tests

```
tests/core_engine/test_rubika_l7_canonical_cohort.py
tests/core_engine/test_l9_batch_a_shadow.py
→ 21 passed (mmp_test_isolation / mmp_isolated_pytest)
```

## L9.8 Persistent shadow deployment requirements

To make production **persistently** run:

`RUBIKA_CANONICAL_SESSION_MODE=shadow`  
`RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=13,23,74`

```
PERSISTENT_CONFIG_CHANGE_REQUIRED=True
CORE_API_RESTART_REQUIRED=True          # pick up env from compose/.env
CELERY_WORKER_RESTART_REQUIRED=True     # if celery is brought back online
RUBIKA_WORKER_REBUILD_REQUIRED=True     # image lacks L7 canonical runtime
RUBIKA_WORKER_RESTART_REQUIRED=True     # after rebuild/recreate
WORKER_PIN_BLOCKS_BATCH_A_RUNTIME_SHADOW=True  # RUBIKA_ACCOUNT_IDS=12,79
```

Worker pin must remain a **separate** controlled gate. Do not combine pin removal with first ENFORCE.

## L9.9 Next controlled rollout (not executed)

A. Persistent SHADOW allowlist `13,23,74` (core_api env + recreate; optional celery)  
B. Verify runtime health / shadow logs  
C. Controlled ENFORCE for **13 only**  
D. Verify  
E. Add **23**  
F. Verify  
G. Add **74**  
H. Verify  

Then separately: rubika_worker rebuild + optional pin change for dynamic discovery.

## Verdict

```
BATCH_A_SHADOW_ONE_SHOT_PASS=True
SAFE_TO_ENABLE_PERSISTENT_BATCH_A_SHADOW=True
EXACT_OPERATOR_INPUT_REQUIRED=Approve the single persistent Batch A shadow deployment action
```
