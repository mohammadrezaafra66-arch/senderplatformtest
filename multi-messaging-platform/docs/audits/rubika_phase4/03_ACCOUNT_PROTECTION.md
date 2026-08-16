# Account Protection

## Escalation

HEALTHY → DEGRADED (elevated failures) → THROTTLED (Phase 3 ladder / elevated rate)
→ QUARANTINED (repeated session/auth or sustained failures)

Single transient timeout does not quarantine.

## Quarantine

- Redis `rubika:quarantine:{id}` (no TTL = durable; TTL = transient)
- Also sets `AccountStatus.RESTING` for restart safety without Redis
- Preflight code: `ACCOUNT_QUARANTINED`
- Blocks connectors, retries, ops path (via preflight), side channels
- No silent account replace

## Recovery / restore

`restore_rubika_account`: rejects BANNED, WRONG_PLATFORM, ACCOUNT_NOT_FOUND,
SESSION_NOT_READY, NOT_QUARANTINED. Clears quarantine/throttle/fail counters,
reactivates RESTING→ACTIVE when session material valid. Audited.
