# Account Selection Precedence (Phase 2)

## Contract

1. **`WorkerPayload.account_id` is authoritative** for campaign, queue, and operational sends.
2. The connector **must not** silently replace the assigned account with another pool member.
3. **Pool / schedule / cooldown / hourly cap** are **eligibility checks** on the assigned account, not selection algorithms.

## Mode behavior

| Mode | Sender | Eligibility extras |
|------|--------|--------------------|
| `bot_api` | `payload.account_id` | session readiness + optional campaign link |
| `user_account` | `payload.account_id` | session + active send window + pool membership for current phase + cooldown + hourly cap + campaign link |

## Auto vs manual campaign

- Manual (`CampaignAccount` rows present): assigned account must be an **enabled** link → else `CAMPAIGN_ACCOUNT_NOT_ALLOWED`.
- Auto (no `CampaignAccount` rows): prepare-time assignment already chose an ACTIVE same-platform account; preflight does not re-pick.

## Side channels (AI / listener / status)

Not campaign-assigned. They select their own pool account at connect time, then run **side_channel** preflight (identity/state/session only) before transport.
