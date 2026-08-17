# 10 REMAINING GAPS

- No Vitest campaign-detail component tests (documented Phase 5.7).
- Dispatch interval sleep (`CAMPAIGN_DISPATCH_INTERVAL_MS`) is configured but not applied as a blocking sleep in the push loop (batch/in-flight are the primary pacing).
- Delayed retry ZSET requires a worker process to drain `rubika:retry:delayed` (implemented in retry.py).
- Index additions were not required after GROUP BY aggregation review.
- Auto-resume of sending after circuit CLOSE is operator-controlled (READY_TO_RESUME), not automatic.
