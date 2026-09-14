# L8 Account23 host-path rollback hardening (source audit notes)

**PRODUCTION_PROMOTION_EXECUTED=False**  
**CURRENT_PHASE=L8_BATCH_A_ACCOUNT23_FINAL_HARDENING**  
**PHASE_STATUS=BLOCKED**

## Fix summary (`scripts/_l8_promote_account23_production.py`)

- After committed promotion, host postchecks go through `host_postcheck_with_auto_rollback`.
- On host postcheck failure + `promotion_committed`: invoke exact `rollback_legacy_promotion` for Account23/Session725 via `L8_EXEC_ROLE=rollback` worker.
- Failure output: `FAILED_STAGE=POST_VERIFY`, `ROLLBACK_PERFORMED=True`, `ROLLBACK_RESULT=PASS|FAIL`; restore verified when PASS; manual intervention when FAIL.
- Pre-commit / non-committed paths do not invoke host rollback.
- No `pg_restore` / full DB restore. Targets remain hardcoded `ACCOUNT_ID=23`, `SESSION_ID=725`.

## Tests

`tests/core_engine/test_l8_account23_host_rollback.py` — isolated regression coverage for host auto-rollback + exact-ID service rollback; Account13 peer unchanged in DB fixtures.

**EXACT_OPERATOR_INPUT_REQUIRED:** Run isolated rollback regression tests after code fix
