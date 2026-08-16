# Health Model

## States

HEALTHY, DEGRADED, THROTTLED, QUARANTINED, CRITICAL, OFFLINE

## Relationship to Phase 3 lifecycle

| Concept | Meaning |
|---------|---------|
| Lifecycle | Sending maturity / rate-policy stage (NEW…NORMAL + overlays) |
| Health | Operational condition from observed failures |

They are **not** collapsed. Health THROTTLED may coincide with lifecycle THROTTLED overlay.

## Signals

session readiness, success/fail window counts, consecutive failures, failure rate,
auth/session errors, transport/timeout/rate-limit, cooldown/throttle, quarantine,
dependency availability, last success/failure, lifecycle stage (context only)

## Windows (configurable)

`RUBIKA_HEALTH_WINDOW_SECONDS`, consecutive / count / ratio thresholds.

Redis keys: `rubika:health:ok|fail:{id}:{bucket}`, `rubika:health:consec:{id}`,
`rubika:health:meta:{id}` — TTL tied to window; restart loses ephemeral counters
(quarantine durable separately).
