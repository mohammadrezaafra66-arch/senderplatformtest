# C1 — Production Reconciliation (Post Docker Recovery)

Generated: 2026-09-01T11:30:00+00:00

## Docker recovery

| Container | State |
|-----------|-------|
| mmp_postgres | running |
| mmp_redis | running |
| mmp_core_api | running (restarted once for preflight batch-runtime parity fix) |
| mmp_frontend | running (C1 image redeployed) |
| mmp_rubika_worker | running |

DOCKER_ENGINE_HEALTHY=True

## M2 unchanged (read-only verify)

| Check | Result |
|-------|--------|
| Session721 | active |
| Session2 | decrypt_failed |
| GLOBAL_ACTIVE | 2→721, 13→729, 23→725, 27→772, 74→724 |
| GLOBAL_ACTIVE_SESSION_COUNT | 5 |
| Workers | [2, 12, 13, 23, 27, 74, 79] |
| Account2 runtime (batch L18) | READY |
| M2_STILL_PASS | True |

No M2 promotion rerun. No session mutation.

## C1 deploy state

| Item | Result |
|------|--------|
| C1_BACKEND_DEPLOYED | True (bind-mount + core_api reload) |
| C1_FRONTEND_BUILD_PRESENT | True |
| C1_FRONTEND_NEW_VERSION_LIVE | True (`senderFilterReady`, `isCampaignEligible`, `campaign_eligible` in bundle) |
| C1_FRONTEND_DEPLOY_REQUIRED | Was True (old bundle lacked C1 strings); completed via `docker compose build frontend` + `up -d --no-deps frontend` |

## Fresh sender inventory (read-only sim)

| Metric | Value |
|--------|--------|
| TOTAL_CAMPAIGN_SENDER_CANDIDATES | 43 |
| READY_SENDER_ACCOUNTS | 2, 13, 23, 27, 74, 79 |
| MANUAL_REVIEW_SENDER_ACCOUNTS | 12, 19, 81, 92 |
| SESSION_ERROR_SENDER_ACCOUNTS | 1 |
| LOGIN_REQUIRED_SENDER_ACCOUNTS | 32 |
| CAPACITY_BLOCKED_SENDER_ACCOUNTS | 0 |

## API truth verification (admin JWT)

Key accounts via `GET /accounts?platform=rubika`:

| Account | runtime_status | campaign_eligible | display_identity |
|---------|----------------|-------------------|------------------|
| 2 | READY | true | 989048249522 |
| 13 | READY | true | 989048249556 |
| 23 | READY | true | 989048249546 |
| 27 | READY | true | 989048249530 |
| 74 | READY | true | 989048249527 |
| 79 | READY | true | 989048241903 |
| 12 | MANUAL_REVIEW | false | 989048249554 |
| 19 | MANUAL_REVIEW | false | 989048249550 |
| 81 | MANUAL_REVIEW | false | 989048249537 |
| 92 | MANUAL_REVIEW | false | 989903858654 |
| 1 | SESSION_ERROR | false | اکانت #1 (short numeric phone rejected) |
| 3 | LOGIN_REQUIRED | false | 989048249560 |

Campaign detail vs preflight (campaign 103, sender 79): both READY / campaign_eligible=true after preflight batch-runtime fix.

## Safety sentinels

- MESSAGE_SENT=False
- OTP_REQUESTED=False
- NEW_SESSION_CREATED=False
- L17 config unchanged (override.yml pin 12,79 preserved)
- No DB migration

## L17 / L18

- RUBIKA_L3_LOGIN_ROUTING=auto_evidence (core_api)
- AUTO_ENROLL_RUBIKA_POOL=true
- RUBIKA_CANONICAL_SESSION_SCOPE=canonical_active
- RUBIKA_WORKER_DISCOVERY_SCOPE=all_eligible (rubika_worker)
- Account12 MANUAL_REVIEW protected
- L17_AUTOMATION_STILL_PASS=True
- L18_STATUS_TRUTH_STILL_PASS=True

## NEXT_SAFE_ACTION

Continue Manual Review remediation for Accounts 12/19/81, then handle Account92 relogin separately.
