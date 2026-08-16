# Rubika Phase 2 — Preflight Baseline (pre-implementation)

**Branch:** `feature/rubika-module`  
**START_HEAD:** `bbf24c7f5eb02351de48b2f15bba9cf35a01cfb7`  
**Phase 1:** structured readiness exists; **not** enforced at worker transport.

Status: SAFE | PARTIAL | BYPASS | UNKNOWN

## Send entry point map

| ID | Entry point | File | Function | Mode | Current checks | Bypass readiness? | External transport? | Risk | Status | Action |
|----|-------------|------|----------|------|----------------|-------------------|---------------------|------|--------|--------|
| EP01 | Platform delivery router | `workers/delivery.py` | `deliver_platform_message` | both | DRY_RUN/SHADOW/REAL/CONNECTORS; USER_ACCOUNT_ENABLED | yes | via EP02/03 | HIGH | BYPASS | Call preflight in connectors (and optionally router) |
| EP02 | Bot API connector | `workers/connectors/rubika.py` | `deliver_rubika_live` | bot_api | load token only | yes | yes (`sendMessage`) | HIGH | BYPASS | **Enforce preflight before HTTP** |
| EP03 | User account connector | `workers/connectors/rubika_user.py` | `deliver_rubika_user_live` | user_account | phase window; pool pick; load session | partial (ignores assigned account) | yes (rubpy send) | HIGH | BYPASS | **Enforce preflight on assigned account; stop silent pool replace** |
| EP04 | Rubika worker | `workers/rubika_worker.py` | `send_message` | both | none beyond EP01 | yes | via EP01 | HIGH | BYPASS | covered if EP02/03 closed |
| EP05 | Queue bridge push | `core_engine/services/queue_bridge.py` | push loop | n/a | sender assignment match | yes (worker still unchecked) | via EP04 | HIGH | PARTIAL | worker-side preflight required |
| EP06 | Campaign prepare/assign | `campaign_sender_assignment.py` | `resolve_campaign_sender_accounts` | n/a | ACTIVE + platform | yes | via queue | MED | PARTIAL | preflight must respect Message.account_id |
| EP07 | Ops test send | `operational_send.py` | `send_account_test_message` | both | `build_live_send_preflight` (readiness) | no* | via EP01 | MED | PARTIAL | reuse central preflight; *user_account still pool-replaced |
| EP08 | Ops preflight API | `operational_send.py` | `build_live_send_preflight` | n/a | env + readiness | n/a | no | LOW | SAFE | extend to call central codes |
| EP09 | AI reply loop | `rubika_ai_response_loop.py` | `send_message` | user_account | pool ACTIVE select | yes | yes | HIGH | BYPASS | gate with central preflight (side_channel) |
| EP10 | Group listener reply | `rubika_group_listener.py` | `update.reply` | user_account | pool select | yes | yes | HIGH | BYPASS | gate with central preflight (side_channel) |
| EP11 | Status bot publish/like/comment | `rubika_status_bot.py` | publish/like/comment | user_account | status pool + own caps | yes | yes | HIGH | BYPASS | gate with central preflight (side_channel) |
| EP12 | Manual script | `scripts/rubika_user_send_test.py` | main | user_account | none | yes | yes | MED | BYPASS | out of production path; document |
| EP13 | OTP login | `rubika_user_session.py` | start/verify | user_account | mode gates | n/a | auth only | LOW | SAFE | no message send |
| EP14 | GUID debug script | `scripts/rubika_user_resolve_guid_debug.py` | main | user_account | none | yes | yes | LOW | BYPASS | non-prod |
| EP15 | Celery message_dispatch | `core_engine/tasks.py` | send_message_task | n/a | unwired for Rubika live | n/a | no | LOW | SAFE | none |

## Deep notes

1. **delivery.py** routes Rubika by `RUBIKA_DELIVERY_MODE`; no readiness.
2. **operational_send** checks readiness then calls `deliver_platform_message` in-process.
3. **user_account** currently **ignores** `WorkerPayload.account_id` and picks from `RubikaAccountPool` — Phase 2 must make assignment authoritative.
4. **rate_limit**: cooldown + hourly used by user pool path only; bot_api has none.
5. **send window**: `resolve_current_phase` only in EP03.
6. **Phase 1 readiness** is API/ops only today.

## Counts (pre-implementation)

- Audited: 15
- BYPASS: 10
- PARTIAL: 3
- SAFE: 2
