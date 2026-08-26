# R2 — Rubika Preflight Contract Audit

**Date:** 2026-08-26  
**Branch:** `remediation/rubika-multi-account-production-20260826_160629`  
**HEAD / R1:** `9dda4df4bec0bd4b8d1c7c5fa0593653ba1dab96`  
**Phase:** R2.1  
**Status:** PASS (audit complete; remediation continues in R2.2+)

## Runtime identity (verified locally)

| Container | Status | WORKER_PLATFORM | WORKER_ACCOUNT_ID | WORKER_EXECUTION_ENABLED |
|-----------|--------|-----------------|-------------------|--------------------------|
| `mmp_rubika_worker` | Created (not running) | rubika | **79** | false |
| `mmp_rubika_worker_pilot12` | Exited | rubika | **12** | false |

Do not assume historical Account1 binding. Effective compose/override identity is Account79 for the main worker and Account12 for the pilot container. Both currently have execution disabled and are not actively consuming.

## Gate matrix

| GATE | FILE | FUNCTION | INPUT | REASON CODE | CAMPAIGN CHECKS? | TRANSPORT CHECKS? | WORKER CHECKS? | DIVERGENCE? |
|------|------|----------|-------|-------------|------------------|-------------------|----------------|-------------|
| Campaign send preflight | `core_engine/services/campaign_preflight.py` | `evaluate_campaign_send_preflight` | `campaign_id`, DB, Redis | `CAMPAIGN_*` + per-account codes | yes | no | no | Reimplements account rules; **misses pool membership** |
| Campaign start gate | `core_engine/services/campaign_control.py` | `start_campaign` | Campaign + Redis pause | Campaign codes via preflight | yes | no | no | Rubika-only preflight before RUNNING |
| Auto-prepare on start | `core_engine/services/campaign_control.py` | `_auto_prepare` | `campaign_id` | Prepare codes; many HTTP errors swallowed | partial | no | no | Defect G — collapses to `CAMPAIGN_NOT_PREPARED` |
| Capacity planner | `core_engine/services/campaign_capacity.py` | `aggregate_campaign_capacity`, `account_ready_now` | `AccountCapacityInput` | Hard/temp code sets | yes | no | no | Pure classifier; does not fetch evidence |
| Controlled dispatch | `core_engine/services/campaign_dispatch.py` | `claim_ready_items`, `blocked_rubika_account_ids` | Running campaigns, Redis | Quarantine / circuit skips | yes | no | no | No quota/window/pool at claim |
| Queue bridge | `core_engine/services/queue_bridge.py` | `push_staged_items_to_worker_queue` | Staged items, Redis, env | Circuit / consent / render | yes | no | partial | `REAL_QUEUE_PUSH_ENABLED` gate |
| Transport Rubika preflight | `core_engine/services/rubika_preflight.py` | `evaluate_rubika_send_preflight` | account, campaign_id, Redis | Full account/session/pool/quota/circuit codes | partial | yes | yes | Authoritative transport gate today |
| Side-channel preflight | `core_engine/services/rubika_preflight.py` | `require_rubika_side_channel_send` | account_id, context | Same minus campaign/pool/window | no | yes | yes | Narrower path for AI/listener/status |
| Session readiness | `core_engine/services/account_session_wiring.py` | `evaluate_account_session_readiness` | Account, delivery mode | `READY`, `SESSION_*`, `ACCOUNT_*` | yes | yes | yes | Shared Phase-1 engine |
| Lifecycle / caps | `core_engine/services/rubika_policy.py` | `resolve_rubika_lifecycle`, `resolve_effective_limits` | Account + snap | Feeds denials | partial | yes | yes | Policy only |
| Phase schedule | `workers/rubika_account_pool.py` | `resolve_current_phase` | `RubikaSenderSchedule` | → `OUTSIDE_SEND_WINDOW` | partial | yes | yes | Campaign uses windows as warnings |
| Phase pool membership | `rubika_preflight` + `rubika_account_pool` | `_account_in_phase_pool`, `list_pool_accounts` | account_id, phase | `ACCOUNT_NOT_IN_ALLOWED_POOL` | **no** | yes | yes | **Major A** |
| Atomic quota reserve | `core_engine/services/rubika_quota.py` | `reserve_send_quota` | Redis rate keys | Cap / interval denies | no | yes | yes | Soft read in campaign only |
| Circuit breaker | `core_engine/services/rubika_circuit.py` | `assert_circuit_allows_send` | Redis circuit keys | `RUBIKA_CIRCUIT_OPEN` | yes | yes | yes | Shared Redis evidence |
| Quarantine | `core_engine/services/rubika_health.py` | `is_account_quarantined` | Redis quarantine | `ACCOUNT_QUARANTINED` | yes | yes | yes | Shared |
| Campaign pause | `campaign_control` + `workers/base_worker.py` | `set_campaign_pause` / `check_campaign_paused` | `campaign:{id}:paused` | Worker requeue event | yes | no | yes | Parses `"true"` only (E) |
| Account pause | `workers/base_worker.py` | `check_account_paused` | `account:{id}:paused` | `account_paused` | no | no | yes | Writer missing / parse `"true"` only |
| System kill switch | `control_service` + workers | `set_kill_switch` / `check_kill_switch` | `system:kill_switch` | `paused_by_kill_switch` | no | no | yes | Parses `"true"` only (E) |
| Env transport kill | `workers/delivery.py` | `deliver_platform_message` | REAL/CONNECTORS/DRY/SHADOW | `real_send_disabled` etc. | no | yes | yes | Canonical Phase-7 kill path |
| User connector | `workers/connectors/rubika_user.py` | `deliver_rubika_user_live` | payload.account_id | Full transport + reserve | partial | yes | yes | Exact account routing |
| Worker payload identity | `workers/base_worker.py` | `validate_payload` | WORKER_* vs payload | PayloadValidationError | no | no | yes | Single-account plane (B/C) |
| Worker factory | `workers/factory.py` | `build_worker` | WORKER_PLATFORM/ACCOUNT_ID | ValueError | no | no | yes | Always single-account RubikaWorker |
| Pool factory | `workers/pool_factory.py` | `build_pool_worker` | WhatsApp only | Rejects rubika | no | no | yes | No Rubika multi-account worker |
| WORKER_EXECUTION_ENABLED | `safety_guard.py` | `assert_worker_execution_disabled` | env | SafetyViolationError | no | no | **no** | Not enforced before LPOP (D) |
| Sender assignment | `campaign_sender_assignment.py` | `resolve_campaign_sender_accounts` | CampaignAccount | prepare-time codes | yes | no | no | Transport rechecks manual links only |
| Ops live-send preflight | `operational_send.py` | `build_live_send_preflight` | account + env | Checklist keys | no | partial | yes | Does not call full Rubika preflight |
| Ops UI display | `rubika_operations.py` | `_preflight_display` | health snapshots | Non-canonical display codes | partial | no | no | Defect J |
| Worker heartbeat | `pool_health.py` / WhatsApp only | `publish_worker_heartbeat` | WhatsApp pool | N/A | no | no | WhatsApp only | No Rubika coverage (F) |

## Defects A–J (verified)

| ID | Defect | Status |
|----|--------|--------|
| A | Campaign vs Transport preflight divergence (pool/window hardness) | Confirmed — R2.2 target |
| B | Single-account Rubika worker plane | Confirmed — R4 target |
| C | Effective identity is Account79 (main) / Account12 (pilot); not Account1 | Confirmed |
| D | `WORKER_EXECUTION_ENABLED` not enforced before queue consumption | Confirmed — R3 target |
| E | Pause/kill parse only `"true"`; `"1"` ignored | Confirmed — R3 target |
| F | Rubika worker coverage / heartbeat missing | Confirmed — R5 target |
| G | Prepare errors swallowed → `CAMPAIGN_NOT_PREPARED` | Confirmed — R6 target |
| H | Tests can mutate production `RubikaSenderSchedule` | Confirmed — R8 target |
| I | Account92 duplicate session/pool possible (no unique session constraint) | Investigate in R7 |
| J | UI readiness codes non-canonical / incomplete | Confirmed — R9 target |

## Layer map

```
Start API → auto_prepare → campaign_preflight → RUNNING
                ↓
         queue_bridge (REAL_QUEUE_PUSH, circuit, consent)
                ↓
         Redis queue:rubika:{account_id}
                ↓
         BaseWorker (kill_switch, pauses)  [parse "true" only]
                ↓
         delivery (DRY_RUN / SHADOW / REAL_MESSAGE / CONNECTORS)
                ↓
         evaluate_rubika_send_preflight + reserve_send_quota → transport
```

## R2.2 implementation notes

- Authoritative account engine: `evaluate_rubika_send_preflight`
- Campaign preflight now consumes that engine for per-account `block_code`
- `consume_circuit_probe=False` keeps campaign preflight read-only on half-open probes
- `resolve_current_phase(..., clock=)` aligns Campaign/Transport window evaluation
- Parity tests: `tests/core_engine/test_rubika_send_gate_parity.py` (monkeypatched phase; no schedule mutation)

## Runtime observation (2026-08-26)

After R2.2 activation, Campaign 4 parity matched Transport:

- Account12 / Account79 → `ACCOUNT_NOT_IN_ALLOWED_POOL` on both layers

Root cause: production schedule `day` (id=195, 8–22) was **inactive**; leftover active test phases (`p6-pool-*`) were selected instead. Accounts 12/79 remain enrolled in pool phase `day` only.

**Operator restore required before ready_accounts=2 can return:**

1. `UPDATE rubika_sender_schedules SET is_active=true WHERE phase='day';`
2. `UPDATE rubika_sender_schedules SET is_active=false WHERE phase LIKE 'p6-%' OR phase LIKE 'r22-%' OR phase LIKE 'day-only%';`

Do not delete production rows. Test isolation fixes in R8 prevent recurrence.
