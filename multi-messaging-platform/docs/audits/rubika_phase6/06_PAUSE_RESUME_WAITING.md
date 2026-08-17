# Pause, resume, waiting

## Operator pause

`stop_campaign`: `Campaign.status=paused` + Redis `campaign:{id}:paused`.
Bridge only claims RUNNING. Worker `check_campaign_paused` requeues in-flight
payloads to the account list (does not send). Survives process restart.

## Safety pause

Circuit OPEN: no new Rubika dispatch; Redis `campaign:{id}:safety_pause`;
derived state `PAUSED_SAFETY`. Campaign is **not** FAILED.

## Resume

Operator start after circuit CLOSED. Preflight may set `ready_to_resume=true`
(`READY_TO_RESUME`). **No automatic send resume.** `resume_policy=operator`.

## WAITING_WINDOW

Outside send window (user_account): start allowed with warning
`CAMPAIGN_OUTSIDE_SEND_WINDOW`; `next_window_start` when determinable.
Dispatch skip + delayed retry, not busy 1s loops.

## WAITING_CAPACITY

Hourly/daily exhausted: warning, not permanent failure. Retry near hour/day
boundary via delayed ZSET.

In-flight at pause: already queued Redis payloads are requeued on pause check;
they are not marked permanent failure.
