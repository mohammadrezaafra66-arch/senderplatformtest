# Rubika Rate Limit Policy (Phase 3)

**Important:** Numeric defaults are **application policy**, not official Rubika
platform guarantees. Operators may change ceilings via WorkerSettings / env.

## Configuration (`WorkerSettings`)

| Setting | Default | Role |
|---------|---------|------|
| `RUBIKA_DAILY_SEND_CAP` | 100 | Ceiling for NORMAL daily |
| `RUBIKA_HOURLY_SEND_CAP` | 50 | Ceiling for NORMAL hourly |
| `RUBIKA_MIN_SEND_DELAY_SECONDS` | 5 | Min interval floor / NORMAL interval |
| `RUBIKA_MAX_SEND_DELAY_SECONDS` | 15 | Jitter upper bound |
| `RUBIKA_JITTER_ENABLED` | true | Enable bounded jitter |
| `RUBIKA_RESERVE_TTL_SECONDS` | 120 | Reservation crash TTL |
| `RUBIKA_FAILURE_THRESHOLD` | 3 | Failures before cooldown |
| `RUBIKA_FAILURE_COOLDOWN_SECONDS` | 300 | Cooldown length |
| `RUBIKA_FAILURE_THROTTLE_SECONDS` | 600 | Throttle length |

## Stage defaults (conservative application defaults)

| Stage | Daily | Hourly | Min interval (s) |
|-------|-------|--------|------------------|
| NEW | 3 | 1 | 90 |
| OBSERVATION | 5 | 2 | 60 |
| LIMITED | 15 | 5 | 30 |
| RAMPING | 50 | 15 | 15 |
| NORMAL | configured caps | configured caps | configured min |

Effective early-stage caps = `min(stage, configured)`.
Effective early-stage min interval = `max(stage, configured min)`.

## Timezone

- Policy day/hour buckets: **Asia/Tehran**
- Send windows: existing `rubika_sender_schedules` (Iran local hour)
- Boundary: `start_hour` inclusive, `end_hour` exclusive; overnight wrap supported

## Codes

| Code | Meaning |
|------|---------|
| `DAILY_CAP_REACHED` | sent_today ≥ daily_cap |
| `HOURLY_CAP_REACHED` | sent_this_hour ≥ hourly_cap |
| `MIN_INTERVAL_ACTIVE` | delay key TTL > 0 |
| `OUTSIDE_SEND_WINDOW` | no active schedule phase |
| `COOLDOWN_ACTIVE` | explicit cooldown meta |
| `ACCOUNT_THROTTLED` | throttle overlay |
| `REDIS_UNAVAILABLE` | safety dependency failed |

## Count semantics

- Preflight: soft read (eligibility)
- Reservation: atomic INCR of daily+hourly **before** transport
- Commit: keep counters; set min-interval delay (jittered)
- Release: DECR on failure
- Definition: quota consumed at reservation (count-before-send with rollback on failure)
