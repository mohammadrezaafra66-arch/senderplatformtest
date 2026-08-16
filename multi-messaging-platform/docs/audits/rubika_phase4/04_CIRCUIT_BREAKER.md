# Circuit Breaker

States: CLOSED → OPEN → HALF_OPEN → CLOSED|OPEN

## Systemic triggers

- Distinct failing accounts ≥ `RUBIKA_CIRCUIT_DISTINCT_ACCOUNTS` (default 3)
- Or high failure count with ≥2 accounts / dependency (account_id=None)

One unhealthy account alone cannot open the breaker.

## OPEN

Blocks all production Rubika transports via preflight `RUBIKA_CIRCUIT_OPEN`
(retryable). No quota reservation consumed.

## HALF_OPEN

Atomic Redis probe budget (`RUBIKA_CIRCUIT_PROBE_BUDGET`, default 1).
Success → CLOSE; failure → OPEN again. Concurrent workers: at most budget probes.

## Persistence

Redis `rubika:circuit:state` + meta. Restart without Redis → CLOSED (fail open
on missing key); sending still fail-closed if Redis unavailable in preflight P0.
