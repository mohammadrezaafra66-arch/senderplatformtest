# Incident Model

Scope: ACCOUNT | SYSTEM  
Status: OPEN | RESOLVED  
Storage: Redis (`rubika:incident:{dedupe}` + open index) — no migration

Dedup key: `{scope}:{category}:{code}:{account_id|none}`  
Repeated hits increment `occurrence_count` and `last_seen_at`.

Dependency Redis outages open SYSTEM incidents without quarantining accounts.
