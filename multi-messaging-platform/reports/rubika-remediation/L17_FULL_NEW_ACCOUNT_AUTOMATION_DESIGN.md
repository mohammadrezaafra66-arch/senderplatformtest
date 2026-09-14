# L17 — Full New-Account Automation Design

**CURRENT_PHASE=L17_FULL_NEW_ACCOUNT_AUTOMATION**  
**PRODUCTION_AUTOMATION_CLEANUP_EXECUTED=False**

## Permanent rules (implemented in code; defaults backward-compatible)

### Login routing — `RUBIKA_L3_LOGIN_ROUTING`

| Value | Behavior |
|-------|----------|
| `pilot` (default) | L16 behavior: V1 OR pilot ID list only |
| `auto_evidence` | L3 when zero sessions OR canonical-managed OR pure L3 debris; legacy-protected otherwise |

Central function: `core_engine/services/rubika_l17_automation.decide_l3_login_routing`

Pilot list remains emergency override under either mode.

### Pool enrollment — `AUTO_ENROLL_RUBIKA_POOL`

When `true`, after successful L3 promote → `ensure_rubika_pool_membership` (savepoint, idempotent).  
Failure → `POOL_ENROLLMENT_FAILED` readiness block; ACTIVE session preserved.

### Discovery — `RUBIKA_WORKER_DISCOVERY_SCOPE`

| Value | Behavior |
|-------|----------|
| `cohort` (default) | L11/L14 canary: empty cohort = dynamic only; non-empty = pin ∪ (dynamic ∩ cohort) |
| `all_eligible` | Permanent: **pin ∪ dynamic eligible** (cohort ignored for inclusion) |

Keep `RUBIKA_ACCOUNT_IDS=12,79`.

### Canonical enforce — `RUBIKA_CANONICAL_SESSION_SCOPE`

| Value | Behavior |
|-------|----------|
| `allowlist` (default) | L15/L16 allowlist |
| `canonical_active` | Enforce iff exactly one ACTIVE + valid identity binding; fail-closed; never max(id) |

### Schedule

`NEW_ACCOUNT_SCHEDULE_AUTOMATION_REQUIRED=False`  
`RubikaSenderSchedule` is global Iran-TZ phase windows — not per-account. Do not auto-create business schedules.

## Account28-shaped chain (code-ready)

Create account (0 sessions) → auto L3 → OTP → promote ACTIVE → auto pool → dynamic eligible → worker via all_eligible → auto enforce scope → dispatch ready.
