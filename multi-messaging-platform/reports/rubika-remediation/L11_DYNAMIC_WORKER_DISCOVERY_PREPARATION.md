# L11 — Dynamic Rubika Worker Discovery Preparation

**CURRENT_PHASE=L11_DYNAMIC_WORKER_DISCOVERY_PREPARATION**  
**PHASE_STATUS=COMPLETE**  
**PRODUCTION_DYNAMIC_DISCOVERY_ENABLED=False**  
**WORKER_PIN_REMOVED=False**  
**PRODUCTION_ENFORCE_ENABLED=False**  
**CURRENT_PRODUCTION_DISCOVERY_MODE=pinned**  
**CURRENT_PRODUCTION_PIN=12,79**

## L11.1 Call graph (forensic)

```
docker rubika_worker
  → workers.run_forever
      → pool_factory.build_pool_worker
           → resolve_rubika_worker_account_ids_from_settings
                → get_dispatch_eligible_rubika_account_ids  (authoritative)
                → resolve_actual_worker_account_ids(mode)
           → RubikaPoolWorker(account_ids=actual)
      → run_forever
           → _heartbeat_loop → publish_worker_heartbeat + publish_account_coverage (TTL)
           → _refresh_accounts_loop → re-resolve desired IDs (idempotent replace_account_ids)
           → run_once → LPOP queue:rubika:{id} → deliver_platform_message
                → session_access.load_account_session_plaintext
                     → load_rubika_runtime_session (canonical shadow/enforce — separate concern)
```

`RUBIKA_ACCOUNT_IDS` is read from `WorkerSettings` / compose override.  
Historical bug: boot discovery results were frozen as `_explicit_account_ids`. L11 sets pin freeze only for `pinned|shadow`.

## L11.3 Eligibility contract

Canonical function: `workers.rubika_worker_discovery.get_dispatch_eligible_rubika_account_ids`.

Predicates (all required):

1. `platform == RUBIKA`
2. `account.status == ACTIVE`
3. current send phase resolvable (`resolve_current_phase`)
4. `rubika_account_pool` membership for that phase
5. session availability:
   - exactly one `ACTIVE` session with material + identity binding, **or**
   - exactly one non-ACTIVE session that decrypts
   - **exclude** duplicate ACTIVE, multi-legacy ambiguous, missing, decrypt-failed, unresolved identity

Discovery never chooses a send session by `max(id)`. Session selection remains in `session_access`.

## L11.4 Modes

| Mode | Actual worker coverage |
|---|---|
| `pinned` (default) | exactly `RUBIKA_ACCOUNT_IDS` |
| `shadow` | exactly pin; dynamic set computed/logged only |
| `dynamic` + empty cohort | full eligible set |
| `dynamic` + cohort | **pin preserved** ∪ (eligible ∩ cohort) |

Config keys:

- `RUBIKA_WORKER_DISCOVERY_MODE=pinned|shadow|dynamic`
- `RUBIKA_WORKER_DISCOVERY_COHORT_IDS=` (temporary canary only)

## L11.11 One-shot shadow probe (read-only)

Audit gates:

```
PROBE_SOURCE_AUDIT_PASS=True
PROBE_DB_READ_ONLY=True
PROBE_REDIS_READ_ONLY=True
PROBE_WORKER_NON_MUTATING=True
PROBE_SESSION_NON_MUTATING=True
PROBE_CONFIG_PROCESS_LOCAL_ONLY=True
PROBE_SECRET_SAFE=True
```

Results:

```
PINNED_SET=[12, 79]
DYNAMIC_ELIGIBLE_SET=[13, 23, 74, 79]
WOULD_ADD=[13, 23, 74]
WOULD_REMOVE=[12]
ACTUAL_WORKER_IDS=[12, 79]
DB_MUTATION_DETECTED=False
REDIS_MUTATION_DETECTED=False
```

Classification highlights:

| account | eligible | reason |
|---:|---|---|
| 13 | True | canonical_active |
| 23 | True | canonical_active |
| 74 | True | canonical_active |
| 79 | True | single_legacy |
| 12 | False | AMBIGUOUS_LEGACY_SESSIONS |
| 1 | False | SESSION_DECRYPT_FAILED |
| 2 | False | AMBIGUOUS_LEGACY_SESSIONS |

Coverage after probe: only 12/79 (unchanged). Batch A cov=False.

## L11.9 Batch A

```
ACCOUNT13_DYNAMIC_ELIGIBLE=True
ACCOUNT23_DYNAMIC_ELIGIBLE=True
ACCOUNT74_DYNAMIC_ELIGIBLE=True
ACCOUNT13_MISSING_PREREQUISITE=
ACCOUNT23_MISSING_PREREQUISITE=
ACCOUNT74_MISSING_PREREQUISITE=
```

Pool enrollment already present for day phase — **no production pool mutation required** for Batch A eligibility.

## L11.12 Isolated tests

```
L11_ISOLATED_TESTS_PASSED=26
L11_ISOLATED_TESTS_FAILED=0
```

## L11.13–14 Next production rollout (NOT executed)

**Phase A** — persistent discovery `shadow` while pin remains `12,79`  
**Phase B** — `dynamic` + cohort e.g. `13` (preserves pin; adds 13)  
**Phase C** — expand cohort / eventually empty cohort  
**Separate later phase** — canonical ENFORCE (do not combine with pin removal)

First persistent discovery rollout mutations:

```
CONFIG_FILE=docker-compose.override.yml
CONFIG_KEYS_TO_CHANGE=
  RUBIKA_WORKER_DISCOVERY_MODE=shadow   # Phase A only
  # keep RUBIKA_ACCOUNT_IDS=12,79
RUBIKA_WORKER_REBUILD_REQUIRED=True     # bake discovery module into worker image
RUBIKA_WORKER_RECREATE_REQUIRED=True
CORE_API_RECREATE_REQUIRED=False        # bind-mounted workers/; optional if API path needed
PRODUCTION_POOL_MUTATION_REQUIRED=False
```

## Verdict

```
DYNAMIC_DISCOVERY_IMPLEMENTED=True
PINNED_MODE_BACKWARD_COMPATIBLE=True
DISCOVERY_SHADOW_NON_MUTATING=True
DISCOVERY_RECONCILIATION_IDEMPOTENT=True
DUPLICATE_WORKER_PROTECTION_PASS=True
SAFE_TO_BEGIN_DYNAMIC_DISCOVERY_PRODUCTION_ROLLOUT=True
EXACT_OPERATOR_INPUT_REQUIRED=Approve first persistent discovery rollout only
```
