# Remaining Gaps

1. Resolved incident history is Redis-ephemeral (OPEN index only) — long-term incident archive not persisted.
2. External alert sinks (email/Telegram webhook) not wired — LoggingAlertSink only.
3. Message/sender deep-link UX beyond campaign_usage / send-log reuse is limited.
4. Frontend component unit tests not added (repo has limited FE test infra); static validation used.
5. Circuit half-open manual probe initiation UI not exposed (automatic HALF_OPEN path remains).
