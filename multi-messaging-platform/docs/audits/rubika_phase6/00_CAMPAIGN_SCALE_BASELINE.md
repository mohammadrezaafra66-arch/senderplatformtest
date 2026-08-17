# Phase 6 — Campaign scale baseline

START_HEAD: `ac5f5355107533297fcb901968c930e080309875`

Branch: `feature/rubika-module`

This audit classifies campaign execution **before** Phase 6 work. Account-level
safety (Phases 1–4) remains authoritative. No real Rubika messages are sent.

## Classification key

- **ALREADY_VERIFIED** — behavior exists and is covered by tests
- **PARTIAL** — primitive exists but campaign-scale wiring is incomplete
- **MISSING** — required for Phase 6
- **CONFLICTING** — two sources of truth or unsafe interaction

## Campaign control (`campaign_control.py`)

| Topic | Status | Notes |
| --- | --- | --- |
| Start → RUNNING + clear Redis pause | ALREADY_VERIFIED | `tests/api/test_campaign_start_stop.py` |
| Stop → PAUSED + Redis pause key | ALREADY_VERIFIED | Survives process restart via Redis |
| Auto-prepare on start | ALREADY_VERIFIED | Fail-closed for GPT/product errors |
| Campaign-level preflight before start | MISSING | Start pushed staged items with no capacity/circuit gate |
| Start dumps `batch_size=500` | PARTIAL | Burst risk for large campaigns |

DB `Campaign.status` enum is **not** extended. Execution safety states must be derived.

## Prepare / assignment / freeze

| Topic | Status | Notes |
| --- | --- | --- |
| Frozen `final_text` + SHA-256 | ALREADY_VERIFIED | Phase 5.7 |
| `WorkerPayload.account_id` authoritative | ALREADY_VERIFIED | Bridge parks mismatch |
| No silent sender replacement | ALREADY_VERIFIED | Prepare-time assignment only |
| Manual vs auto sender scope | ALREADY_VERIFIED | `CampaignAccount` empty = auto |
| GPT/product at prepare only | ALREADY_VERIFIED | Phase 5.5 / 5.6 |

## Queue bridge / workers

| Topic | Status | Notes |
| --- | --- | --- |
| Atomic claim `FOR UPDATE SKIP LOCKED` | ALREADY_VERIFIED | `test_push_concurrency.py` |
| Incomplete staged items ignored | ALREADY_VERIFIED | No `rendered_message_id` |
| Fair per-campaign dispatch | MISSING | Global `ORDER BY id` monopolizes |
| Max in-flight / backpressure | MISSING | |
| Per-account in-flight (Redis) | MISSING | Phase 3 quota is send-boundary only |
| Circuit OPEN stops new dispatch | PARTIAL | Worker preflight denies send; bridge still queued |
| Account-aware skip of blocked senders | MISSING | Would claim then fail in worker |
| Retry uses exponential sleep | PARTIAL | 1s-class storms for daily/window/circuit |
| Delayed retry ZSET | MISSING | |
| Duplicate send | PARTIAL | Unique `dedupe_key` + claim; no send lease |

## Capacity / preflight

| Topic | Status | Notes |
| --- | --- | --- |
| Account preflight | ALREADY_VERIFIED | Phase 2 |
| Quota reservation | ALREADY_VERIFIED | Phase 3 Redis Lua, Asia/Tehran |
| Circuit / quarantine | ALREADY_VERIFIED | Phase 4 |
| Campaign aggregate capacity | MISSING | |
| Completion estimate | MISSING | Dashboard `eta_seconds` always null |
| GET `/campaigns/{id}/preflight` | MISSING | |
| Fail closed on Redis | PARTIAL | Account send fail-closed; campaign start did not |

## Pause / waiting

| Topic | Status | Notes |
| --- | --- | --- |
| Operator pause (DB + Redis) | ALREADY_VERIFIED | In-flight Redis payloads requeued by worker |
| Safety pause (circuit) | MISSING | Campaign stayed RUNNING |
| WAITING_WINDOW / WAITING_CAPACITY | MISSING | Derived states only |
| Auto-resume | MISSING | Operator resume is the policy |

## UI / Protection Center

| Topic | Status | Notes |
| --- | --- | --- |
| Campaign detail start/stop | ALREADY_VERIFIED | |
| Capacity / readiness section | MISSING | |
| Per-account capacity table | MISSING | |
| Start blocker UX | PARTIAL | Generic API error only |
| Protection Center campaign impact | PARTIAL | Quarantine/circuit message counts; no running/paused-by-circuit |

## Indexes (no speculative migration)

Existing: `messages.campaign_id`, `messages.account_id`, `staged_queue_items.campaign_id`, `staged_queue_items.status`, `campaign_recipients (campaign_id, send_status)`.

GROUP BY account/status can use these. **MIGRATIONS = NONE**.

## Ordering guarantee

No strict global campaign order. Per-recipient frozen assignment and exact text matter more than FIFO across the campaign.
