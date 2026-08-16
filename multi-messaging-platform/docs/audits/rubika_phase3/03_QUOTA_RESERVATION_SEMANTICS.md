# Quota Reservation Semantics (Phase 3)

## Flow

1. **Preflight eligibility** — soft counters + lifecycle; no mutation of quota.
2. **Reserve** — Lua script atomically:
   - denies if min-interval delay active
   - denies if daily ≥ cap or hourly ≥ cap
   - INCR daily + hourly
   - SET `rubika:reserve:{account}:{token}` with TTL
3. **Transport**
4. **Commit** (success) — DEL reserve marker; SET delay key for min interval / jitter.
5. **Release** (failure/abort) — if reserve marker exists: DEL marker + DECR counters.

## Crash recovery

If a worker dies after reserve:

- Reservation key expires via TTL
- Counters remain incremented → **fail-safe** (capacity not oversubscribed)
- Capacity recovers at Iran day/hour bucket rollover

## Concurrency property

With remaining quota = 1, two concurrent `reserve_send_quota` calls yield
**at most one** success (proven in Phase 3 tests via Redis Lua).

## Why not process-local Lock

Authoritative protection is Redis atomic eval — safe across workers/replicas.
