# Rubika Phase 4 — Health / Failure Baseline (pre-implementation)

**Branch:** `feature/rubika-module`  
**START_HEAD:** `4a081022fb09c5fa6e884eb03347a81868a45aa8`  
**Phase 3:** PASS (policy, quota reservation, lifecycle, fail-closed Redis)

Status legend: VERIFIED | PARTIAL | MISSING | CONFLICTING

## Failure source map

| SOURCE | File/function | Mode | Current code | Retryable? | Changes status? | Lifecycle? | Failure state? | Cooldown? | Throttle? | Stop account? | Stop campaign/system? | Observability | Gap |
|--------|---------------|------|--------------|------------|-----------------|------------|----------------|-----------|-----------|---------------|----------------------|---------------|-----|
| Session missing | preflight Layer D | both | SESSION_MISSING | no | no | — | no | no | no | blocks send | no | preflight log | VERIFIED |
| Session invalid | preflight / user connector | both | SESSION_INVALID / rubika_user_session_invalid | no | REQUIRES_LOGIN via pool | SUSPENDED | no Phase4 | no | no | yes | no | pool last_error | PARTIAL — no health taxonomy |
| Session decrypt | readiness | both | SESSION_DECRYPT_FAILED | no | no | — | no | no | no | blocks | no | readiness | VERIFIED gate; MISSING health signal |
| Account missing | preflight | both | ACCOUNT_MISSING | no | n/a | — | no | no | no | n/a | no | preflight | VERIFIED |
| Banned | preflight | both | ACCOUNT_BANNED | no | already BANNED | SUSPENDED | no | no | no | yes | no | preflight | VERIFIED |
| Requires login | preflight | both | ACCOUNT_REQUIRES_LOGIN | no | already | SUSPENDED | no | no | no | yes | no | preflight | VERIFIED |
| Resting | preflight | both | ACCOUNT_DISABLED | no | RESTING | THROTTLED | no | no | Redis throttle overlay | yes | no | preflight | PARTIAL vs quarantine |
| Rate-limit response | rubika_user RubikaTooRequests | user | rubika_user_rate_limited | yes | mark_account_failed RESTING optional | — | record_send_failure | yes≥3 | yes≥6 | soft | no | logger | PARTIAL — bot_api not recorded |
| HTTP/API error | connectors | both | rubika_*_api_error / http_error | varies | user path marks RESTING sometimes | — | user only | via failcount | via failcount | soft | no | WorkerResult | PARTIAL |
| Timeout | bot connector | bot | rubika_timeout | yes | no | — | no | no | no | no | no | WorkerResult | MISSING health incr |
| Transport error | bot connector | bot | rubika_transport_error | yes | no | — | no | no | no | no | no | WorkerResult | MISSING health incr |
| Redis outage | preflight Layer G | user+side | REDIS_UNAVAILABLE | yes | no | — | fail-closed | no | no | no | no | preflight | VERIFIED fail-closed; MISSING system incident |
| DB error | workers | both | unexpected | yes | no | — | no | no | no | no | no | exception log | MISSING taxonomy |
| Quota / policy deny | preflight Layer F | user | DAILY/HOURLY/MIN_INTERVAL/WINDOW/COOLDOWN | yes | no | overlays | no | existing | existing | no | no | details.policy | VERIFIED policy; not health guilt |
| Unknown connector | user unexpected | user | rubika_user_unexpected_error | yes | no | — | no | no | no | no | no | exception | MISSING classifier |
| Side-channel deny | require_rubika_side_channel_send | side | same codes | varies | no | — | no | no | no | blocks | no | continue | VERIFIED gate; MISSING circuit |
| Worker crash | reservation TTL | user | n/a | — | no | — | counters kept | no | no | no | no | Phase3 docs | VERIFIED quota semantics |
| System kill switch | control_service | global | pause workers | — | no | — | n/a | n/a | n/a | all platforms | yes | Redis | VERIFIED pattern to mirror; not Rubika-specific |
| Campaign pause | campaign_control | campaign | requeue | — | PAUSED | — | no | no | no | campaign | campaign | Redis+DB | VERIFIED |

## Capability classification (Phase 4 targets)

| Capability | Status |
|------------|--------|
| Failure taxonomy | MISSING |
| Health states (vs lifecycle) | MISSING (lifecycle ≠ health) |
| Rolling failure windows | PARTIAL (failcount only) |
| Central record_success/failure for health | PARTIAL (quota-only ladder) |
| Account quarantine code | MISSING |
| Durable quarantine | MISSING (RESTING used loosely) |
| Operator restore with gates | PARTIAL (pool restore exists; not quarantine-aware) |
| Global Rubika circuit breaker | MISSING |
| HALF_OPEN probe budget | MISSING |
| Account vs system incidents | MISSING |
| Incident dedup | MISSING |
| Circuit on side channels | MISSING |
| Dependency ≠ account guilt | PARTIAL (Redis fail-closed but no incident class) |

## Architecture note

Phase 3 **lifecycle** = sending maturity / rate policy stage.  
Phase 4 **health** = operational condition. Keep both; map overlays carefully.
