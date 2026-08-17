# Backpressure and fairness

Backpressure sources:

1. Staged QUEUED+PUSHING vs `CAMPAIGN_DISPATCH_MAX_IN_FLIGHT`
2. Redis per-account in-flight Lua (`rubika:inflight:count:{account_id}`)
3. Send lease SET NX (`rubika:send:lease:{message_id}`)
4. Circuit OPEN / Redis unknown → do not claim Rubika rows (fail closed)

Event: `campaign_dispatch_backpressure`. Item returns to READY (not SKIPPED).

Fairness: when N>1 running campaigns with READY items, each cycle gives
`min(PER_CAMPAIGN, batch/N)` slots. A 10k campaign cannot starve a 100-message
campaign across cycles. Not perfect WRR.

Distributed: Redis Lua is the authority; process-local locks are not.
Worker crash: inflight member TTL expires; slot returns.
