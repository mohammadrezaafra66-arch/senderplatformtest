# Remaining Gaps (after Phase 1)

## Intentionally deferred

- Warm-up lifecycle
- Rate-limit architecture redesign
- Health score / circuit breaker / incident engine
- Campaign safety engine / weighted sender selection
- Group / AI / status scheduler redesign
- Protection center / stress scaling

## Minor leftovers

1. No ChannelSession prune of superseded rows (latest-row is correct but table grows).
2. Accounts UI OTP panel is still manually toggled; pool UI auto-shows on `requires_login`.
3. Worker preflight does not yet uniformly refuse sends using structured readiness codes (Phase 2).

## Next exact phase

**PHASE 2 — RUBIKA PREFLIGHT / READINESS ENFORCEMENT**
