# PHASE4_PROTECTION_MATRIX

| signal | scope | category | severity | health impact | account action | system action | retryable | preflight code | incident? | tested? |
|--------|-------|----------|----------|---------------|----------------|---------------|-----------|----------------|-----------|---------|
| session/auth invalid | ACCOUNT | session/auth | critical | CRITICAL/QUARANTINED | quarantine + REQUIRES_LOGIN | — | no | ACCOUNT_QUARANTINED / ACCOUNT_REQUIRES_LOGIN | yes | yes |
| transport/timeout | ACCOUNT | transport/timeout | warning | DEGRADED→THROTTLED | cooldown/throttle ladder | systemic count | yes | ACCOUNT_THROTTLED / COOLDOWN | yes | yes |
| rate-limit response | ACCOUNT | rate_limit | warning | DEGRADED | cooldown/throttle | systemic | yes | — | yes | yes |
| Redis outage | SYSTEM | dependency_redis | critical | — | none (no guilt) | incident + may open circuit | yes | REDIS_UNAVAILABLE | yes | yes |
| multi-account failures | SYSTEM | transport | critical | — | — | OPEN circuit | yes | RUBIKA_CIRCUIT_OPEN | yes | yes |
| single-account failures | ACCOUNT | transport | warning | local only | throttle/quarantine | no OPEN | yes | local codes | yes | yes |
| policy denial | ACCOUNT | policy | info | none | none | none | yes | DAILY/HOURLY/… | no | Phase3 |
| quarantine | ACCOUNT | policy | critical | QUARANTINED | block all sends | — | no | ACCOUNT_QUARANTINED | yes | yes |
| circuit OPEN | SYSTEM | policy | critical | — | block all | OPEN | yes | RUBIKA_CIRCUIT_OPEN | yes | yes |
| banned | ACCOUNT | auth | critical | OFFLINE | never auto-restore | — | no | ACCOUNT_BANNED | no | yes |
