# L15 — Batch A Canonical ENFORCE Staged Rollout

**CURRENT_PHASE=L15_BATCH_A_CANONICAL_ENFORCE**  
**PHASE_STATUS=COMPLETE**  
**ROLLBACK_PERFORMED=False**  
**CANONICAL_ENFORCE_BATCH_A_ENABLED=True**

## L15.1 — Config / service provenance

| Item | Value |
|------|-------|
| `CANONICAL_CONFIG_FILE` | `docker-compose.override.yml` |
| `CORE_API_CANONICAL_CONFIG_CONSUMER` | True |
| `RUBIKA_WORKER_CANONICAL_CONFIG_CONSUMER` | True |
| `CELERY_CANONICAL_CONFIG_CONSUMER` | False (exited; env present but not running; left alone) |

Both `core_api` and `rubika_worker` carry `RUBIKA_CANONICAL_SESSION_MODE` / `RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS`. Each stage recreated **only** those two services (`--force-recreate --no-deps`). No image rebuild (canonical runtime already present in worker image).

## L15.2 — Predeploy hard gates

All gates passed while still in SHADOW:

- mode=`shadow`, allowlist=`13,23,74`
- ACTIVE 13→729, 23→725, 74→724; all `SHADOW_MATCH`
- discovery=`dynamic`, cohort=`13,23,74`, pin=`12,79`
- actual workers=`[12,13,23,74,79]`; queues all 0
- MessageAttempt baseline=`5`; LoginChallenge baseline=`0`
- GLOBAL_ACTIVE_SESSION_COUNT=`3`

## L15.3 — Shadow rollback backup

```
ROLLBACK_COPY=docker-compose.override.yml.before-l15-enforce-20260831_104203.bak
SHA256=0a81439e9c137da452f3a96f1598d1507439ba5728c20c8d60afaf77544df18b
SIZE=2214
STAGE=shadow_full_batch_a
```

Semantics verified: mode=shadow, allowlist=13,23,74, discovery=dynamic, cohort=13,23,74, pin=12,79.

Proven stage backups also retained:

- `docker-compose.override.yml.l15-e1-proven.bak` (enforce / 13)
- `docker-compose.override.yml.l15-e2-proven.bak` (enforce / 13,23)
- `docker-compose.override.yml.l15-e3-proven.bak` (enforce / 13,23,74)

## Stage E1 — Account13 only

Persist: `MODE=enforce`, `ALLOWLIST=13`. Discovery/pin unchanged.

| Check | Result |
|-------|--------|
| Account13 source | `canonical_enforce` session **729** |
| reconnect / identity / dispatch | PASS |
| Account23/74 | legacy authoritative (725 / 724) |
| workers | `[12,13,23,74,79]` stable 3 cycles |
| counts | msg=5, login=0 unchanged |

**E1_ACCOUNT13_ENFORCE_PASS=True**

## Stage E2 — Add Account23

Persist: `MODE=enforce`, `ALLOWLIST=13,23`.

| Check | Result |
|-------|--------|
| 13 | canonical_enforce **729** healthy |
| 23 | canonical_enforce **725**; reconnect/identity/dispatch PASS |
| 74 | still legacy **724** |
| workers / counts | unchanged |

**E2_ACCOUNT23_ENFORCE_PASS=True**

## Stage E3 — Add Account74 (full Batch A)

Persist: `MODE=enforce`, `ALLOWLIST=13,23,74`.

| account | source | session | reconnect | identity | dispatch | worker |
|--------:|--------|--------:|:---------:|:--------:|:--------:|:------:|
| 13 | canonical_enforce | 729 | PASS | PASS | PASS | PASS |
| 23 | canonical_enforce | 725 | PASS | PASS | PASS | PASS |
| 74 | canonical_enforce | 724 | PASS | PASS | PASS | PASS |
| 12 | pinned legacy | 657/728 unchanged | — | — | — | PASS |
| 79 | pinned legacy | 600 unchanged | — | — | — | PASS |

**E3_ACCOUNT74_ENFORCE_PASS=True**

## L15.4 — Fail-closed proof

Isolated network `mmp_test_isolation` (not production):

```
pytest tests/core_engine/test_rubika_l7_canonical_cohort.py -k
  enforce_canonical_failure_no_max_id_fallback
  or enforce_affects_allowlisted_only
  or enforce_uses_active_not_max_id
  or duplicate_canonical_active
→ 4 passed
```

Proves allowlisted ENFORCE failure → `CanonicalSessionError` / `SessionInvalidError` (fail closed), **not** legacy max(id) fallback. No production session corruption.

**ENFORCE_FAIL_CLOSED_VERIFIED=True**

## L15.5 — Final production state

```
CANONICAL MODE=enforce
CANONICAL ALLOWLIST=13,23,74
WORKER DISCOVERY MODE=dynamic
WORKER DISCOVERY COHORT=13,23,74
PIN=12,79
ACTUAL WORKERS=[12,13,23,74,79]
13=729  23=725  74=724
GLOBAL_ACTIVE_SESSION_COUNT=3
MESSAGE_SENT=False
OTP_REQUESTED=False
ROLLBACK_PERFORMED=False
```

## Boundaries respected

- No OTP, no send, no campaign start
- Pin 12,79 retained; discovery cohort unchanged
- Account12 / Account79 / Account2 not canonicalized
- No migrations / login recovery
- Celery left exited; Postgres/Redis/frontend not restarted

## Next safe action

```
NEXT_SAFE_ACTION=Select one no-session Rubika account for controlled OTP lifecycle pilot
SAFE_TO_BEGIN_OTP_LIFECYCLE_PILOT=True
```
