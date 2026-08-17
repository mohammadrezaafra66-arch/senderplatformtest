# Completion estimation

`estimate_account_completion` / campaign max-across-accounts (bottleneck).

Inputs: remaining assigned per account, remaining today, effective daily
throughput (window hours × min(hourly_cap, 3600/min_interval)), current Iran time.

Output: `estimated_completion_at`, `estimated_duration_seconds`, `policy_days`,
`confidence` (`exact` / `bounded` / `unknown` / `blocked`), `limitations`.

Marked **estimate_only**. Never used as an authorization bypass.

Multi-day example: 1000 messages / 200 effective per policy day ≈ 5 days,
subject to windows. Temporary constraints do not fail the campaign on day 1.

Hard-blocked senders with remaining assigned messages → completion `blocked`
(those messages cannot move to another account).
