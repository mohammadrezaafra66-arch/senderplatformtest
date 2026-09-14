# L8 Account13 Final Source Audit (hardened re-audit)

**PRODUCTION_PROMOTION_EXECUTED=False**  
**CURRENT_PHASE=L8_BATCH_A_ACCOUNT13_FINAL_AUDIT**  
**PHASE_STATUS=BLOCKED**

Hardened file: `scripts/_l8_promote_account13_production.py` only.

## Hardening applied

1. **Backup gates** — `verify_backup_before_mutation`: exists, size>0, SHA recompute match, meta provenance (`source_db=mmp_db`, path/size/sha), freshness ≤30m; called after create and again immediately before mutation.
2. **No auth bypass** — removed `L8_INNER` shortcut. Every entry calls `authorize()` requiring exact `L8_ALLOW_PRODUCTION_PROMOTION=1` and `L8_PRODUCTION_TARGET=account13`. `L8_EXEC_ROLE=host|worker` only selects flow after auth.
3. **COMPLETE hard assertions** — host `assert_host_postconditions` requires Account13 ACTIVE=729, loaders match, queue13=0, global ACTIVE=1, total session count unchanged, 23/74/12/79 unchanged, unrelated session inventory unchanged (excluding 729), MessageAttempt + LoginChallenge counts unchanged. Failure → `GateFailure` / `PHASE_STATUS=BLOCKED`, never COMPLETE.
4. **Structured failure** — `FAILED_STAGE/GATE/EXPECTED/ACTUAL/ROLLBACK_PERFORMED/ROLLBACK_RESULT`.

## Re-audit flags

```
POSITIVE_AUTHORIZATION_REQUIRED=True
TARGET_ACCOUNT_LOCKED_TO_13=True
TARGET_SESSION_LOCKED_TO_729=True
BACKUP_NONEMPTY_HARD_GATE=True
BACKUP_SHA_REVERIFY_HARD_GATE=True
BACKUP_PROVENANCE_HARD_GATE=True
INNER_PATH_CAN_MUTATE_WITHOUT_AUTH=False
PRECHECKS_HARD_BLOCKING=True
USES_REVIEWED_PROMOTION_SERVICE=True
DIRECT_ACTIVE_BYPASS_PRESENT=False
POST_VERIFY_ALL_HARD_ASSERTED=True
NON_TARGET_INVARIANTS_HARD_ASSERTED=True
EXACT_ID_ROLLBACK_DEFINED=True
ROLLBACK_TOUCHES_ONLY_13_729=True
SAFE_TO_PROMOTE_ACCOUNT13_PRODUCTION=True
PRODUCTION_PROMOTION_EXECUTED=False
```

**EXACT_OPERATOR_INPUT_REQUIRED:** Approve Account13/Session729 production promotion after hardened re-audit
