# PHASE6 CAPACITY MATRIX

| account | assigned | health | readiness | daily_remaining | hourly_remaining | window | cooldown | eligible_now | bottleneck | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| A healthy | 200 | healthy | ready | remaining | remaining | open | none | yes | maybe | assignment vs remaining |
| B cooldown | 200 | healthy | ready | remaining | remaining | open | until | no | temporary | COOLDOWN_ACTIVE |
| C quarantined | 200 | unhealthy | blocked | remaining | remaining | open | none | no | blocked | quarantine |
| D requires_login | 200 | healthy | requires_login | remaining | remaining | open | none | no | blocked | session |
| E healthy | 200 | healthy | ready | remaining | remaining | open | none | yes | maybe | assignment vs remaining |

Manual selection: only listed assigned accounts.

Automatic: persisted `RenderedMessage.account_id` / `CampaignAccount` only.

Bottleneck: unused capacity of other accounts is **not** applied to overloaded assignment.
