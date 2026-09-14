# Junk Account/Campaign Cleanup Result

Committed: 2026-09-02T10:11:10.881156+00:00

## Pre-exec hash gate

- CLEANUP_PLAN_HASH_MATCH=True (`8c491cbb5d7174bff661376a44853453e5c19307767f5f2b939fbb39ef53a7c3`)
- CLEANUP_SCRIPT_HASH_MATCH=True (`2263649485728ff0f996983325a1b9ef4a02cc8721d44676a6eb6148cf5febc0`)
  - Note: approved script hash is text-mode (LF-normalized) SHA256 of `_junk_range_execute.py`, matching FK gate method.

## Transaction

- CLEANUP_TRANSACTION_COMMITTED=True
- CLEANUP_TRANSACTION_ROLLED_BACK=False

## Counts

- ACCOUNTS_DELETED=39
- CAMPAIGNS_DELETED=56

## Preservation

- ACCOUNT2269_PRESERVED=True
- CAMPAIGN183_PRESERVED=True
- CAMPAIGN191_PRESERVED=True
- CAMPAIGN199_PRESERVED=True
- CAMPAIGN232_PRESERVED=True
- CAMPAIGN233_PRESERVED=True
- CAMPAIGN233_ATTEMPTS=2
- RUNNING_CAMPAIGNS_PRESERVED=True

## Safety

- ORPHANED_REFERENCES_CREATED=0
- LIVE_OPERATIONAL_DATA_AFFECTED=0
- SUCCESSFUL_SEND_HISTORY_AFFECTED=0
- LIVE_QUEUE_ITEMS_AFFECTED=0
- LIVE_SESSIONS_AFFECTED=0
- remain_deleted_accounts=[]
- remain_deleted_campaigns=[]

## Health

- POSTGRES_HEALTHY=True
- CORE_API_HEALTHY=True
- FRONTEND_HEALTHY=True

## CLEANUP_PASS

CLEANUP_PASS=True

## Deleted account IDs

[2223, 2224, 2225, 2226, 2227, 2228, 2229, 2230, 2231, 2232, 2243, 2244, 2245, 2246, 2247, 2248, 2249, 2250, 2251, 2252, 2263, 2264, 2265, 2266, 2267, 2268, 2270, 2271, 2272, 2273, 2274, 2275, 2276, 2277, 2278, 2308, 2309, 2310, 2311]

## Deleted campaign IDs

[128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179, 180, 181, 182, 184, 185, 186, 187, 188, 189, 190, 192, 193, 194, 195, 196, 197, 198, 225, 226, 227, 228, 229, 230, 231]

## Rowcounts

```json
{
  "message_attempts": 0,
  "staged_queue_items": 33,
  "rendered_messages": 33,
  "messages": 33,
  "campaign_recipients": 40,
  "campaign_accounts": 40,
  "campaigns": 56,
  "channel_sessions": 19,
  "rubika_account_pool": 19,
  "rubika_login_challenges": 0,
  "rate_policies": 0,
  "account_send_settings": 0,
  "accounts": 39,
  "sent_registry_nulled": 0
}
```
