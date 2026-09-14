# L8 Account23 Final Source Audit

**PRODUCTION_PROMOTION_EXECUTED=False**  
**CURRENT_PHASE=L8_BATCH_A_ACCOUNT23_FINAL_AUDIT**  
**PHASE_STATUS=BLOCKED**

Audited file: `scripts/_l8_promote_account23_production.py`  
Helpers: `core_engine/services/rubika_legacy_promotion.py`, `rubika_session_promotion.promote_validated_session`, `rubika_candidate_prover.RealRubikaCandidateProver`, `rollback_legacy_promotion`, `verify_post_promotion`.

No production mutation performed.

## 1. Target lock

- Auth on every path (`ENTRY` / `HOST` / `WORKER`): `L8_ALLOW_PRODUCTION_PROMOTION=1` and `L8_PRODUCTION_TARGET=account23`.
- Runtime IDs are module constants `ACCOUNT_ID = 23`, `SESSION_ID = 725` — not read from env/CLI.
- Promotion/rollback/loaders always use those constants.

**TARGET_ACCOUNT_LOCKED_TO_23=True**  
**TARGET_SESSION_LOCKED_TO_725=True**

## 2. Mutation scope

- `promote_proven_legacy_rubika_session(account_id=23, session_id=725)` only loads/locks that session + Account23.
- Lifecycle: LEGACY_UNCLASSIFIED → prove → identity bind/verify (Account23 only) → VALIDATING → `promote_validated_session` → ACTIVE.
- `promote_validated_session` may SUPERSEDE prior ACTIVE rows for the same `account_id` only; Account23 ACTIVE count is gated to 0 precheck, so no supersede expected.
- No Campaign / Message / MessageAttempt / Pool / Schedule / LoginChallenge writes in script or promotion helpers.
- Prover: decrypt + authenticated reconnect + identity probe only (explicitly no send/OTP).

**MUTATION_SCOPE_EXACT=True**  
**UNRELATED_ROW_MUTATION_PRESENT=False**

## 3. Backup hard gates

`verify_backup_before_mutation`: exists, size>0, SHA recompute vs backup + meta, size match, `source_db=mmp_db`, path/meta consistency, freshness ≤30m. Called after create and again immediately before docker mutation.

**BACKUP_NONEMPTY_HARD_GATE=True**  
**BACKUP_SHA_REVERIFY_HARD_GATE=True**  
**BACKUP_PROVENANCE_HARD_GATE=True**

## 4. Redis auth

Same Account13 fix: `docker exec mmp_core_api` probe uses `get_settings().REDIS_URL` in-process; argv not echoed (`_run_quiet`); errors sanitized; auth/missing URL/connection/malformed/negative → `GateFailure`. Queue key `queue:rubika:23`.

**REDIS_QUEUE_AUTH_FIX_PRESENT=True**  
**REDIS_SECRET_HARDCODED=False**  
**REDIS_SECRET_PRINTED=False**  
**QUEUE_PRECHECK_FAILS_CLOSED=True**

## 5. Precheck hard gates

Host blocking: Account23 exists; Session725→23:legacy_unclassified; ACTIVE count=0; queue23=0; no running campaign; mode off / V1 not on; Account13 exactly `729:active`; GLOBAL_ACTIVE=1; Account74 not active.

Decrypt / structure / authenticated reconnect / identity match / guid empty-or-same: enforced inside `promote_proven_legacy_rubika_session` **before** VALIDATING/ACTIVE commit (fail → no SUCCESS commit).

**PRECHECKS_HARD_BLOCKING=True**

## 6. Promotion path

Uses reviewed `promote_proven_legacy_rubika_session` + `RealRubikaCandidateProver` + `promote_validated_session`. No direct `session_status=ACTIVE` assignment in the script.

**USES_REVIEWED_PROMOTION_SERVICE=True**  
**DIRECT_ACTIVE_BYPASS_PRESENT=False**

## 7. Postcheck

Inner hard checks + host `assert_host_postconditions`: ACTIVE 725; loaders match; LEGACY_CANONICAL_MATCH; reconnect+identity; queue23=0; GLOBAL_ACTIVE=2; Account13 still `729:active` unchanged; 74/12/79 unchanged; total sessions / MessageAttempt / LoginChallenge unchanged; unrelated inventory excluding 725 unchanged. Failure → GateFailure / never COMPLETE.

**POST_VERIFY_ALL_HARD_ASSERTED=True**  
**NON_TARGET_INVARIANTS_HARD_ASSERTED=True**

## 8. Rollback

`rollback_legacy_promotion(account_id=23, session_id=725)`: exact ID + account match; ACTIVE→LEGACY_UNCLASSIFIED; identity restore only if `newly_bound`. Invoked on inner postcheck failure after commit. No `pg_restore` / full-DB restore in script.

**EXACT_ID_ROLLBACK_DEFINED=True**  
**ROLLBACK_TOUCHES_ONLY_23_725=True**  
**AUTO_FULL_RESTORE_PRESENT=False**

Residual: host-path `assert_host_postconditions` failure after a successful inner commit does not itself call rollback (same as Account13 runner); COMPLETE is still withheld.

## 9. External effects

**SCRIPT_SENDS_MESSAGES=False**  
**SCRIPT_REQUESTS_OTP=False**  
**SCRIPT_ENQUEUES_MESSAGES=False**  
**SCRIPT_RESTARTS_WORKERS=False**  
**SCRIPT_ENABLES_CANONICAL_MODE=False** (host forces `RUBIKA_CANONICAL_SESSION_MODE=off` into worker)

## Final flags

```
TARGET_ACCOUNT_LOCKED_TO_23=True
TARGET_SESSION_LOCKED_TO_725=True

MUTATION_SCOPE_EXACT=True
UNRELATED_ROW_MUTATION_PRESENT=False

BACKUP_NONEMPTY_HARD_GATE=True
BACKUP_SHA_REVERIFY_HARD_GATE=True
BACKUP_PROVENANCE_HARD_GATE=True

REDIS_QUEUE_AUTH_FIX_PRESENT=True
QUEUE_PRECHECK_FAILS_CLOSED=True

PRECHECKS_HARD_BLOCKING=True
USES_REVIEWED_PROMOTION_SERVICE=True
DIRECT_ACTIVE_BYPASS_PRESENT=False

POST_VERIFY_ALL_HARD_ASSERTED=True
NON_TARGET_INVARIANTS_HARD_ASSERTED=True

EXACT_ID_ROLLBACK_DEFINED=True
ROLLBACK_TOUCHES_ONLY_23_725=True

SAFE_TO_PROMOTE_ACCOUNT23_PRODUCTION=True

PRODUCTION_PROMOTION_EXECUTED=False

CURRENT_PHASE=L8_BATCH_A_ACCOUNT23_FINAL_AUDIT
PHASE_STATUS=BLOCKED
EXACT_OPERATOR_INPUT_REQUIRED=Approve Account23/Session725 production promotion after source audit
```
