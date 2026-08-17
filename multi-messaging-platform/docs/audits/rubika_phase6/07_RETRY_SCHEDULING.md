# Retry scheduling

`campaign_retry_schedule.retry_hint_for_code` + worker `_schedule_retry`.

| Code | Hint |
| --- | --- |
| COOLDOWN_ACTIVE | `cooldown_until` |
| MIN_INTERVAL_ACTIVE | `next_allowed_at` / retry_after |
| HOURLY_CAP_REACHED | next Iran hour bucket |
| DAILY_CAP_REACHED | next policy day 00:00 Asia/Tehran |
| OUTSIDE_SEND_WINDOW | `next_window_start` |
| RUBIKA_CIRCUIT_OPEN | `open_until` |
| REDIS_UNAVAILABLE | 30s delayed |

Delays > 15s go to Redis ZSET `rubika:retry:delayed` (no worker sleep-for-hours).
Bridge `flush_due_delayed_retries` before claim.

Storm prevention: skip TTL on blocked accounts; delayed queue; no 1-second
policy retries.

Retry copies frozen payload JSON (`final_text` unchanged). No GPT/product.
