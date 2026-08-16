# Rubika Lifecycle Model (Phase 3)

## Policy states (application view)

| State | Meaning |
|-------|---------|
| NEW | No warming anchor and no prior use |
| OBSERVATION | Warming day 1–2 |
| LIMITED | Warming day 3–6 |
| RAMPING | Warming day 7–13 |
| NORMAL | Warming day 14+ |
| THROTTLED | Redis throttle overlay and/or AccountStatus.RESTING |
| COOLDOWN | Explicit failure cooldown (Redis meta) |
| SUSPENDED | BANNED / REQUIRES_LOGIN / non-ACTIVE |

These are **not** a new DB enum. They are derived deterministically.

## Mapping from AccountStatus

| AccountStatus | Lifecycle |
|---------------|-----------|
| ACTIVE (+ warming) | NEW…NORMAL via `warming_started_at` |
| RESTING | THROTTLED (preflight: `ACCOUNT_DISABLED`) |
| BANNED | SUSPENDED (`ACCOUNT_BANNED`) |
| REQUIRES_LOGIN | SUSPENDED (`ACCOUNT_REQUIRES_LOGIN`) |

Redis overlays:

- `rubika:cooldown:{id}` → COOLDOWN (`COOLDOWN_ACTIVE`)
- `rubika:throttle:{id}` → THROTTLED (`ACCOUNT_THROTTLED`)
- `config:delay:{id}` → min-interval (`MIN_INTERVAL_ACTIVE`), not lifecycle COOLDOWN

## Persistence / restart safety

- `accounts.warming_started_at` and `accounts.status` are durable.
- Process restart does **not** reset a mature account to NEW.
- First successful send may set `warming_started_at` via `ensure_warming_started`.
- Redis cooldown/throttle are ephemeral and re-evaluated after restart.

## Transitions

Valid (derived, not stored as a state machine):

- NEW → OBSERVATION → LIMITED → RAMPING → NORMAL as calendar days advance from warming anchor
- any sendable → COOLDOWN / THROTTLED via failure counters
- COOLDOWN/THROTTLED expiry → re-evaluate prior warming stage (not forced NORMAL)
- ACTIVE → REQUIRES_LOGIN / BANNED / RESTING via existing pool failure helpers

Invalid:

- SUSPENDED (banned/login) must never be sendable
- silent skip of lifecycle evaluation on user_account runtime path
