# Remaining Gaps

## Closed in Phase 2

- Central preflight
- Connector transport gates (bot_api + user_account)
- Assigned-account precedence for user_account
- Cooldown / hourly / window / Redis fail-closed
- Side-channel gates for AI / listener / status

## Residual / later

- Dev scripts (`rubika_user_send_test`, GUID debug) still direct
- Warm-up / daily ramp / health score / circuit breaker (Phase 3+)
- Weighted failover when assigned sender unavailable (explicitly out of scope)
- ChannelSession prune of superseded rows

## Next exact phase

**PHASE 3 — RUBIKA RATE LIMIT + LIFECYCLE / WARM-UP HARDENING**
