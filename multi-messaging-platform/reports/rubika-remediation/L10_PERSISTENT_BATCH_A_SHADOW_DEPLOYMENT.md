# L10 — Persistent Batch A Shadow Deployment

**CURRENT_PHASE=L10_PERSISTENT_BATCH_A_SHADOW_DEPLOYMENT**  
**PHASE_STATUS=COMPLETE**  
**ROLLBACK_PERFORMED=False**  
**PRODUCTION_ENFORCE_ENABLED=False**  
**WORKER_PIN_REMOVED=False**

## Hard gates (before any recreate)

```
CONFIG_FILE=docker-compose.override.yml
ROLLBACK_FILE=docker-compose.override.yml.before-l10-batch-a-shadow-20260831_130359.bak
ROLLBACK_SHA256=330a72f35763f98386169c0238cb3824383e25b3a27e32a3660b59043752a0f3
CURRENT_CONFIG_SHA256=3c454008883051b16f14ffec7ba218efb642ba0a1f5f42997360a17a9d9507bf
MODE=shadow
ALLOWLIST=13,23,74
WORKER_PIN=12,79
CONFIG_ROLLBACK_GATE_PASS=True
CONFIG_EXACT_VALUE_GATE_PASS=True
HARD_GATE_PASS=True
```

Assertions enforced programmatically by `scripts/_l10_hard_gate_config.py` (reject enforce / non-Batch-A allowlist / pin ≠ 12,79).

## Services affected

| service | action | result |
|---|---|---|
| core_api | force-recreate (bind mounts already had L7) | health PASS; mode=shadow; allowlist=13,23,74 |
| celery_worker | none (left `exited`; no `session_access` import path) | CELERY_WORKER_HEALTH_PASS=True (intentionally untouched) |
| rubika_worker | targeted rebuild + force-recreate | alive; L7 present; pin=12,79; mode=shadow; allowlist=13,23,74 |
| postgres / redis / frontend | not touched | — |

## Postdeploy invariants

```
PERSISTENT_MODE=shadow
PERSISTENT_ALLOWLIST=13,23,74
RUBIKA_ACCOUNT_IDS=12,79
GLOBAL_ACTIVE_SESSION_COUNT=3
Account13 ACTIVE=729
Account23 ACTIVE=725
Account74 ACTIVE=724
Account12 unchanged (657/728 legacy_unclassified)
Account79 unchanged (600 legacy_unclassified)
queues 12/79/13/23/74 = 0
MessageAttempt=5 unchanged
LoginChallenge=0 unchanged
MESSAGE_SENT=False
OTP_REQUESTED=False
```

## Persistent shadow read check

| account | legacy | canonical | result |
|---:|---:|---:|---|
| 13 | 729 | 729 | SHADOW_MATCH |
| 23 | 725 | 725 | SHADOW_MATCH |
| 74 | 724 | 724 | SHADOW_MATCH |

`WORKER_PIN_BLOCKS_BATCH_A_RUNTIME_SHADOW=True` (intentional — pin remains 12,79).

## Boundary

Did not enable ENFORCE, remove worker pin, promote sessions, OTP, send, migrate, or start campaigns.

## Next

`NEXT_SAFE_ACTION=Dynamic worker discovery preparation, without enabling ENFORCE in the same change`
