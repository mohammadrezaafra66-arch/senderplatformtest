# Controlled dispatch

Settings (`core_engine.config.Settings` / `workers.config.WorkerSettings`):

| Key | Default | Role |
| --- | --- | --- |
| `CAMPAIGN_DISPATCH_BATCH_SIZE` | 50 | Claim/push batch (Celery uses this; start too) |
| `CAMPAIGN_DISPATCH_MAX_IN_FLIGHT` | 200 | Cap QUEUED+PUSHING for campaigns in this cycle |
| `CAMPAIGN_DISPATCH_PER_CAMPAIGN_BATCH` | 25 | Fairness bound; split across running campaigns |
| `CAMPAIGN_DISPATCH_INTERVAL_MS` | 0 | Reserved pacing (not per-account min interval) |
| `RUBIKA_MAX_IN_FLIGHT_PER_ACCOUNT` | 8 | Redis concurrency, **not** send quota |
| `RUBIKA_INFLIGHT_TTL_SECONDS` | 180 | Crash recovery |

Claim remains Postgres `FOR UPDATE SKIP LOCKED`. Fairness: rotate campaign ids,
bounded take per campaign when several run.

Blocked Rubika accounts (circuit, quarantine, skip TTL) are **not claimed**.
Items stay READY. No sender reassignment.

Phase 5.7 hash/`final_text` checks unchanged. GPT/product not invoked.
