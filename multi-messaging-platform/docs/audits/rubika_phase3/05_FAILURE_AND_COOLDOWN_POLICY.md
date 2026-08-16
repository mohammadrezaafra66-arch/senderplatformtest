# Failure and Cooldown Policy (Phase 3)

## Explicit cooldown

- Redis key: `rubika:cooldown:{account_id}` JSON `{account_id, reason, started_at, until}`
- Also sets `config:delay:{account_id}` for spacing
- Preflight code: `COOLDOWN_ACTIVE` with `cooldown_until` + `reason`
- Lifecycle view: `COOLDOWN`
- After expiry: re-evaluate warming stage (LIMITED/RAMPING/NORMAL) — **not** forced to NORMAL

## Throttle

- Redis key: `rubika:throttle:{account_id}`
- Preflight: `ACCOUNT_THROTTLED`
- AccountStatus.RESTING remains `ACCOUNT_DISABLED` (Phase 2 compat) with lifecycle THROTTLED

## Failure foundation (`record_send_failure`)

Configurable:

- `RUBIKA_FAILURE_THRESHOLD` (default 3) → enter cooldown
- `2 × threshold` → enter throttle
- Single transient failure does **not** ban

Auth/session failures still use existing `mark_account_failed(requires_relogin=True)` → REQUIRES_LOGIN / SUSPENDED.

## What this is not

No invented Rubika ban heuristics, no ML reputation, no full circuit breaker
(Phase 4).
