# Capacity model

Pure functions: `core_engine.services.campaign_capacity`

Timezone: **Asia/Tehran** (`policy_now`). Application policy caps only
(`rubika_policy` + Redis quota snapshots). Not invented Rubika platform limits.

Per assigned account (persisted `Message.account_id`):

- remaining_daily / remaining_hourly from snapshot vs effective stage caps
- window / cooldown / min-interval / health / quarantine / lifecycle
- `eligible_now`, `immediate_capacity` (min-interval ⇒ at most 1 “now”)
- today capacity: remaining daily ∩ remaining this hour + future window hours
  × hourly_cap, **capped by that account’s assigned remaining**

Aggregation is **not** `N_accounts × daily_cap`. Unused capacity of account B
cannot cover account A’s assignment (no silent reassignment).

bot_api: daily/hourly/warmup are N/A (Phase 3). Planner reports
`today_confidence=not_applicable` and limitation `bot_api_quota_not_applicable`.

Manual selection: only `CampaignAccount` rows. Auto: persisted assignment
distribution only — not a live global healthy pool.

Unknown values stay `null` with explicit confidence/limitations.
