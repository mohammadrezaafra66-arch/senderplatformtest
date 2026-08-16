# Rubika Phase 2 Preflight Audit — Send Path Map

**Scope:** Every runtime path that can reach Rubika external transport  
(HTTP Bot API `sendMessage` **or** rubpy `send_message` / `send_photo` / `update.reply` / Rubino publish).  
**Goal:** Identify where Phase 1 `evaluate_account_session_readiness` is / is not enforced before transport.  
**Date:** 2026-08-16  
**Code base:** `feature/rubika-module` lineage

---

## Verdict (one line)

**Most Rubika transports bypass Phase 1 readiness.** Only operational live test-send (`build_live_send_preflight`) calls `evaluate_account_session_readiness` before delivery; campaign workers, user-pool connector, AI reply, keyword reply, and status publish do not.

---

## Architecture notes (required deep-dives)

### 1) How `workers/delivery.py` routes Rubika

```text
deliver_platform_message(platform, payload, settings)
  ├─ DRY_RUN / SHADOW_MODE / REAL_MESSAGE_SENDING_ENABLED / CHANNEL_CONNECTORS_ENABLED gates
  └─ platform == "rubika"
       ├─ settings.RUBIKA_DELIVERY_MODE.strip().lower() == "user_account"
       │    ├─ require RUBIKA_USER_ACCOUNT_ENABLED
       │    └─ deliver_rubika_user_live(payload, settings)   # rubpy
       └─ else
            └─ deliver_rubika_live(payload, settings)        # botapi.rubika.ir
```

- Mode comes from **WorkerSettings env**, not from `evaluate_account_session_readiness`.
- `delivery.py` does **not** call `normalize_rubika_delivery_mode` / `rubika_mode.py` at call time (WorkerSettings validator does at settings load).
- No session readiness, no account status, no pool, no send-window checks at this layer (those live only inside `deliver_rubika_user_live` partially).

### 2) How `operational_send` works for Rubika

Path: `POST /accounts/{id}/send-test` → `send_account_test_message` → (live) `build_live_send_preflight` → `deliver_platform_message`.

| Step | Rubika behavior |
|------|-----------------|
| Recipient | Treated as **bot platform**: requires `chat_id` / handle (`channel_handle`) |
| Preflight (live only) | Env gates + not banned + **`evaluate_account_session_readiness`** |
| Settings | `build_operational_worker_settings` — copies WA mode explicitly; Rubika mode comes from `WorkerSettings()` env |
| Transport | Direct `await deliver_platform_message(...)` (no Redis queue for Rubika) |
| user_account caveat | Preflight checks **the requested account**, but `deliver_rubika_user_live` **re-selects from pool** and may send from a different account |

Dry-run skips preflight and never hits external transport.

### 3) Is campaign `WorkerPayload.account_id` respected in `rubika_user`?

**No — pool selection wins.**

In `deliver_rubika_user_live`:

1. `resolve_current_phase(db)` from `rubika_sender_schedules`
2. `RubikaAccountPoolManager.get_available_account(phase=...)` — first ACTIVE pool row for that phase not in cooldown / under hourly cap
3. `payload.account_id` is **never read** for client load or send

Implications:

- Campaign prepare (`phase4_prepare` + `resolve_campaign_sender_accounts`) **does** assign `account_id` into staged payload / `Message.account_id`.
- Queue bridge pushes to `queue:rubika:{message.account_id}` and validates payload/message account match.
- `RubikaWorker` / `BaseWorker` validate `payload.account_id == worker.account_id`.
- After those checks, **user_account connector ignores the assigned account** and picks from day/night pool.

`deliver_rubika_live` (bot_api) **does** use `payload.account_id` via `load_rubika_bot_token(payload.account_id)`.

### 4) Existing rate_limit helpers

File: `workers/rate_limit.py`

| Helper | Used by Rubika? | Notes |
|--------|-----------------|-------|
| `is_min_delay_active` / `set_min_delay` | **Yes** — user connector + pool | Redis `config:delay:{account_id}` |
| `is_hourly_cap_reached` / `record_successful_send` / `hourly_send_count` | **Yes** — user connector + pool | Cap from `RUBIKA_HOURLY_SEND_CAP` (default 50) |
| `can_send_daily` / warming ramp / `WHATSAPP_DAILY_SEND_CAP` | **No** | WhatsApp pool worker only |
| Status bot like/comment caps | Separate keys `rubika:status:like:*` / `rubika:status:comment:*` | Not `rate_limit.py` |

Bot API path (`deliver_rubika_live`) has **no** min-delay / hourly cap.

### 5) Send window / schedule checks (`resolve_current_phase`)

File: `workers/rubika_account_pool.py`

- Reads active rows from `rubika_sender_schedules` (Iran TZ hour).
- Returns phase name (`day` / `night` / …) or `None` if outside all windows.
- **Only** called from `deliver_rubika_user_live`.
- Not applied to: bot_api, AI loop, keyword reply, status bot, operational send (except indirectly if live user_account path hits connector).

### 6) Existing preflight

| Mechanism | Where | Covers Rubika? |
|-----------|-------|----------------|
| `evaluate_account_session_readiness` | `account_session_wiring.py` | Status/API; **not** worker transport |
| `build_live_send_preflight` | `operational_send.py` | Yes for live ops test only |
| Delivery env gates | `delivery.py` | DRY_RUN / REAL_SEND / CONNECTORS / user_account flag |
| User connector gates | `deliver_rubika_user_live` | Phase window + pool + session load + rate limits — **not** full Phase 1 readiness codes |
| Campaign prepare | `resolve_campaign_sender_accounts` | ACTIVE + platform match only — **no** session readiness |

---

## Entry-point catalog

Legend — **Status**:

- `SAFE` — cannot reach external transport without Phase 1 readiness (or no transport)
- `PARTIAL` — some gates exist but incomplete / wrong-account / wrong-mode gaps
- `BYPASS` — can reach external transport without Phase 1 readiness
- `UNKNOWN` — needs runtime confirmation

Legend — **Can bypass Phase 1 readiness?** means: can this path send without a passing `evaluate_account_session_readiness` for the account that actually transports.

---

### EP01 — Delivery router

| Field | Value |
|-------|-------|
| **ID** | EP01 |
| **Entry point name** | Platform delivery router |
| **File path** | `/workspace/multi-messaging-platform/workers/delivery.py` |
| **Function name** | `deliver_platform_message` |
| **Mode** | both (routes by `RUBIKA_DELIVERY_MODE`) |
| **Current checks before transport** | `DRY_RUN`, `SHADOW_MODE`, `REAL_MESSAGE_SENDING_ENABLED`, `CHANNEL_CONNECTORS_ENABLED`; for user_account also `RUBIKA_USER_ACCOUNT_ENABLED` |
| **Can bypass Phase 1 readiness?** | yes (no readiness call) |
| **Can reach external transport?** | yes (delegates) |
| **Risk** | HIGH |
| **Status** | BYPASS |
| **Action required** | Add Mode+readiness gate before connector call; reject wrong-mode / not-ready with Phase 1 codes |

---

### EP02 — Bot API connector (HTTP)

| Field | Value |
|-------|-------|
| **ID** | EP02 |
| **Entry point name** | Rubika Bot API live send |
| **File path** | `/workspace/multi-messaging-platform/workers/connectors/rubika.py` |
| **Function name** | `deliver_rubika_live` → `send_rubika_text_message` → `request_rubika_api` |
| **Mode** | bot_api |
| **Current checks before transport** | Load `API_TOKEN` session for `payload.account_id`; resolve `chat_id`; no account status / readiness / rate limit / send window |
| **Can bypass Phase 1 readiness?** | yes (only “session decryptable + parseable token”) |
| **Can reach external transport?** | yes — `POST {RUBIKA_API_BASE_URL}/{token}/sendMessage` |
| **Risk** | HIGH |
| **Status** | BYPASS |
| **Action required** | Enforce readiness (`API_TOKEN`, ACTIVE, mode=bot_api) before HTTP; optionally rate-limit |

---

### EP03 — User-account connector (rubpy)

| Field | Value |
|-------|-------|
| **ID** | EP03 |
| **Entry point name** | Rubika user-account live send |
| **File path** | `/workspace/multi-messaging-platform/workers/connectors/rubika_user.py` |
| **Function name** | `deliver_rubika_user_live` (`send_photo` / `send_message`) |
| **Mode** | user_account |
| **Current checks before transport** | `resolve_current_phase`; pool `get_available_account` (ACTIVE + cooldown + hourly cap); load `RUBIKA_SESSION`; connect; resolve GUID (`add_address_book`); **ignores `payload.account_id`** |
| **Can bypass Phase 1 readiness?** | partial — ACTIVE+session load mimic readiness loosely; no structured codes; no envelope validation via readiness; wrong account vs campaign assignment |
| **Can reach external transport?** | yes — rubpy send + address book |
| **Risk** | HIGH |
| **Status** | BYPASS |
| **Action required** | Call `evaluate_account_session_readiness` on selected (or assigned) account; decide assigned-vs-pool policy; refuse if not ready |

---

### EP04 — Rubika Redis worker

| Field | Value |
|-------|-------|
| **ID** | EP04 |
| **Entry point name** | RubikaWorker campaign/queue consumer |
| **File path** | `/workspace/multi-messaging-platform/workers/rubika_worker.py` (+ `workers/base_worker.py`) |
| **Function name** | `RubikaWorker.send_message` → `deliver_platform_message` |
| **Mode** | both (via delivery settings) |
| **Current checks before transport** | Kill switch / account pause / campaign pause; payload platform+account match worker; then EP01 gates |
| **Can bypass Phase 1 readiness?** | yes |
| **Can reach external transport?** | yes |
| **Risk** | HIGH |
| **Status** | BYPASS |
| **Action required** | Preflight readiness in worker or in EP01 before send; compose multi-account workers if user_account pool is intended |

---

### EP05 — Queue bridge (campaign → Redis)

| Field | Value |
|-------|-------|
| **ID** | EP05 |
| **Entry point name** | Staged queue push |
| **File path** | `/workspace/multi-messaging-platform/core_engine/services/queue_bridge.py` |
| **Function name** | `push_staged_items_to_worker_queue` |
| **Mode** | n/a (enqueue) — leads to both |
| **Current checks before transport** | `REAL_QUEUE_PUSH_ENABLED`; campaign RUNNING; consent; payload/message `account_id` consistency; **no session readiness** |
| **Can bypass Phase 1 readiness?** | yes (enqueues not-ready accounts) |
| **Can reach external transport?** | no directly — yes via EP04 |
| **Risk** | HIGH |
| **Status** | BYPASS |
| **Action required** | Reject/skip staged items when Rubika account readiness fails (Phase 2 enqueue gate) |

---

### EP06 — Campaign prepare / sender assignment

| Field | Value |
|-------|-------|
| **ID** | EP06 |
| **Entry point name** | Campaign message prepare |
| **File path** | `/workspace/multi-messaging-platform/core_engine/services/phase4_prepare.py` (+ `campaign_sender_assignment.py`, `phase4_utils.build_staged_queue_payload`) |
| **Function name** | `prepare_campaign_messages` / `resolve_campaign_sender_accounts` |
| **Mode** | n/a (staging) — leads to both |
| **Current checks before transport** | Sender ACTIVE + platform match; round-robin assign `account_id`; **no readiness / mode / session** |
| **Can bypass Phase 1 readiness?** | yes (stages not-ready senders) |
| **Can reach external transport?** | no directly |
| **Risk** | MED |
| **Status** | BYPASS |
| **Action required** | Optionally require readiness at prepare time; document that user_account ignores assigned id at send time |

---

### EP07 — Operational live test send (API)

| Field | Value |
|-------|-------|
| **ID** | EP07 |
| **Entry point name** | Account send-test (live) |
| **File path** | `/workspace/multi-messaging-platform/core_engine/services/operational_send.py` (+ `core_engine/api/accounts.py` `send_test_message`) |
| **Function name** | `send_account_test_message` / `build_live_send_preflight` |
| **Mode** | both |
| **Current checks before transport** | Live: `OPS_LIVE_SEND_API_ENABLED`, real-send, connectors, not DRY_RUN, not banned, **`evaluate_account_session_readiness`**; then EP01 |
| **Can bypass Phase 1 readiness?** | no for live (preflight blocks); **partial** in user_account because transport account may differ from preflighted account |
| **Can reach external transport?** | yes when live confirmed |
| **Risk** | MED |
| **Status** | PARTIAL |
| **Action required** | For user_account: either force assigned account in connector for ops tests, or preflight the pool-selected account; pass `RUBIKA_*` into operational worker settings explicitly |

---

### EP08 — Operational dry-run / preflight endpoint

| Field | Value |
|-------|-------|
| **ID** | EP08 |
| **Entry point name** | Live-send preflight API |
| **File path** | `/workspace/multi-messaging-platform/core_engine/services/operational_send.py` (+ `accounts.py` `live_send_preflight`) |
| **Function name** | `build_live_send_preflight` |
| **Mode** | n/a (check only) |
| **Current checks before transport** | Same checklist as EP07 without sending |
| **Can bypass Phase 1 readiness?** | n/a |
| **Can reach external transport?** | no |
| **Risk** | LOW |
| **Status** | SAFE |
| **Action required** | Reuse as shared worker/enqueue preflight helper in Phase 2 |

---

### EP09 — Rubika AI response send (“assistant”)

| Field | Value |
|-------|-------|
| **ID** | EP09 |
| **Entry point name** | AI conversation reply send |
| **File path** | `/workspace/multi-messaging-platform/workers/rubika_ai_response_loop.py` |
| **Function name** | `RubikaAiResponseLoop._process_pending_once` → `client.send_message` |
| **Mode** | user_account |
| **Current checks before transport** | Pool select `phase=listener` ACTIVE; load session; `_connect_authenticated`; **no** Phase 1 readiness, send window, or delivery env gates |
| **Can bypass Phase 1 readiness?** | yes |
| **Can reach external transport?** | yes |
| **Risk** | HIGH |
| **Status** | BYPASS |
| **Action required** | Gate with readiness + mode asserts; share client-load helper that refuses not-ready |

No separate “assistant” module exists; this loop is the assistant/AI outbound path.

---

### EP10 — Group listener keyword auto-reply

| Field | Value |
|-------|-------|
| **ID** | EP10 |
| **Entry point name** | Keyword auto-reply |
| **File path** | `/workspace/multi-messaging-platform/workers/rubika_group_listener.py` |
| **Function name** | `RubikaGroupListener._handle_message` → `update.reply` |
| **Mode** | user_account |
| **Current checks before transport** | Allowed group + keyword match; listener pool account; session load; **no** readiness / rate limit / send window |
| **Can bypass Phase 1 readiness?** | yes |
| **Can reach external transport?** | yes |
| **Risk** | HIGH |
| **Status** | BYPASS |
| **Action required** | Same readiness gate as EP09 before any outbound reply |

---

### EP11 — Status bot publish (content schedule)

| Field | Value |
|-------|-------|
| **ID** | EP11 |
| **Entry point name** | Rubika status / Rubino publish cycle |
| **File path** | `/workspace/multi-messaging-platform/workers/rubika_status_bot.py` |
| **Function name** | `_run_publish_cycle` (`rubino.add_picture` / `add_video`); also `_run_like_and_comment_cycle` |
| **Mode** | user_account |
| **Current checks before transport** | Pool `phase=status` ACTIVE; load session; content `scheduled_at <= now` + not published; like/comment Redis caps; **no** Phase 1 readiness / delivery env gates |
| **Can bypass Phase 1 readiness?** | yes |
| **Can reach external transport?** | yes (Rubino + underlying rubpy) |
| **Risk** | HIGH |
| **Status** | BYPASS |
| **Action required** | Readiness before connect/publish; treat likes/comments as side transports in Phase 2 scope |

Content schedule **API** (`core_engine/api/rubika.py` CRUD `/content-schedule`) only writes DB — transport is EP11.

---

### EP12 — Manual / script user send

| Field | Value |
|-------|-------|
| **ID** | EP12 |
| **Entry point name** | Dev script rubika user send |
| **File path** | `/workspace/multi-messaging-platform/scripts/rubika_user_send_test.py` |
| **Function name** | `main` → `deliver_rubika_user_live` |
| **Mode** | user_account |
| **Current checks before transport** | Account exists + platform=rubika; then EP03 checks only |
| **Can bypass Phase 1 readiness?** | yes |
| **Can reach external transport?** | yes |
| **Risk** | MED |
| **Status** | BYPASS |
| **Action required** | Call readiness before deliver; document that `account_id` CLI arg is ignored by pool selection |

---

### EP13 — OTP login (`send_code`) — auth, not message campaign

| Field | Value |
|-------|-------|
| **ID** | EP13 |
| **Entry point name** | Rubika user login start |
| **File path** | `/workspace/multi-messaging-platform/core_engine/services/rubika_user_session.py` (+ API register) |
| **Function name** | `start_rubika_user_login` → `client.send_code` |
| **Mode** | user_account (gated by `assert_rubika_user_login_allowed`) |
| **Current checks before transport** | Mode + `RUBIKA_USER_ACCOUNT_ENABLED` via API asserts |
| **Can bypass Phase 1 readiness?** | n/a (login creates readiness) |
| **Can reach external transport?** | yes (OTP SMS path — not campaign message) |
| **Risk** | LOW |
| **Status** | SAFE (for Phase 2 message preflight; keep mode gates) |
| **Action required** | Out of Phase 2 message-send scope; do not block login with session readiness |

---

### EP14 — Login verify / `register_device`

| Field | Value |
|-------|-------|
| **ID** | EP14 |
| **Entry point name** | Rubika user login verify |
| **File path** | `/workspace/multi-messaging-platform/core_engine/services/rubika_user_session.py` |
| **Function name** | `verify_rubika_user_login` |
| **Mode** | user_account |
| **Current checks before transport** | Mode gate; Redis state; then rubpy sign-in + `register_device` |
| **Can bypass Phase 1 readiness?** | n/a |
| **Can reach external transport?** | yes (auth/device) |
| **Risk** | LOW |
| **Status** | SAFE (auth path) |
| **Action required** | None for message preflight |

---

### EP15 — Celery / queue_manager / message_dispatch

| Field | Value |
|-------|-------|
| **ID** | EP15 |
| **Entry point name** | Legacy Celery dispatch |
| **File path** | `/workspace/multi-messaging-platform/core_engine/tasks.py` (`send_message_task`), `queue_manager.py`, `message_dispatch.py` |
| **Function name** | `send_message_via_queue` / `dispatch_message` |
| **Mode** | n/a |
| **Current checks before transport** | Consent + DRY_RUN/SHADOW; `_send_to_channel` only if injected (tests); **production path does not wire Rubika connectors** |
| **Can bypass Phase 1 readiness?** | n/a in prod (no Rubika transport wired) |
| **Can reach external transport?** | no (unless a custom handler is injected) |
| **Risk** | LOW |
| **Status** | SAFE |
| **Action required** | Keep Rubika out of this path; real campaign path is EP05→EP04 |

---

### EP16 — GUID resolve side-effect (`add_address_book`)

| Field | Value |
|-------|-------|
| **ID** | EP16 |
| **Entry point name** | Address-book GUID resolve |
| **File path** | `/workspace/multi-messaging-platform/workers/connectors/rubika_user.py` |
| **Function name** | `_resolve_object_guid` |
| **Mode** | user_account |
| **Current checks before transport** | Nested under EP03 after pool select |
| **Can bypass Phase 1 readiness?** | yes (with EP03) |
| **Can reach external transport?** | yes (rubpy `add_address_book`) |
| **Risk** | MED |
| **Status** | BYPASS |
| **Action required** | Covered by EP03 readiness gate (must run before address book) |

---

### EP17 — Debug GUID script

| Field | Value |
|-------|-------|
| **ID** | EP17 |
| **Entry point name** | GUID resolve debug script |
| **File path** | `/workspace/multi-messaging-platform/scripts/rubika_user_resolve_guid_debug.py` |
| **Function name** | script main → `load_rubika_user_client` + address-book style calls |
| **Mode** | user_account |
| **Current checks before transport** | minimal |
| **Can bypass Phase 1 readiness?** | yes |
| **Can reach external transport?** | yes |
| **Risk** | LOW (ops-only) |
| **Status** | BYPASS |
| **Action required** | Document / optional readiness assert for safety |

---

## Status summary matrix

| ID | Name | Mode | External? | Phase1 bypass? | Status | Risk |
|----|------|------|-----------|----------------|--------|------|
| EP01 | delivery router | both | yes | yes | BYPASS | HIGH |
| EP02 | bot API connector | bot_api | yes | yes | BYPASS | HIGH |
| EP03 | user connector | user_account | yes | partial/yes | BYPASS | HIGH |
| EP04 | RubikaWorker | both | yes | yes | BYPASS | HIGH |
| EP05 | queue_bridge | n/a | via EP04 | yes | BYPASS | HIGH |
| EP06 | campaign prepare | n/a | via EP05 | yes | BYPASS | MED |
| EP07 | ops live send-test | both | yes | no / partial | PARTIAL | MED |
| EP08 | ops preflight API | n/a | no | n/a | SAFE | LOW |
| EP09 | AI reply loop | user_account | yes | yes | BYPASS | HIGH |
| EP10 | keyword reply | user_account | yes | yes | BYPASS | HIGH |
| EP11 | status publish/like | user_account | yes | yes | BYPASS | HIGH |
| EP12 | send_test script | user_account | yes | yes | BYPASS | MED |
| EP13 | OTP send_code | user_account | auth | n/a | SAFE | LOW |
| EP14 | login verify | user_account | auth | n/a | SAFE | LOW |
| EP15 | Celery dispatch | n/a | no (prod) | n/a | SAFE | LOW |
| EP16 | add_address_book | user_account | yes | yes | BYPASS | MED |
| EP17 | GUID debug script | user_account | yes | yes | BYPASS | LOW |

**Counts:** BYPASS 11 · PARTIAL 1 · SAFE 5

---

## Call-graph (campaign happy path)

```text
prepare_campaign_messages (EP06)
  → StagedQueueItem.queue_payload{account_id, channel=rubika, final_text, ...}
Celery / campaign_control
  → push_staged_items_to_worker_queue (EP05)
      → Redis RPUSH queue:rubika:{account_id}
RubikaWorker.run_once (EP04)
  → validate_payload (account_id must match WORKER_ACCOUNT_ID)
  → deliver_platform_message (EP01)
       ├─ bot_api → deliver_rubika_live (EP02) uses payload.account_id
       └─ user_account → deliver_rubika_user_live (EP03) IGNORES payload.account_id → pool
```

## Call-graph (side loops — user_account only)

```text
rubika_group_listener (EP10) ──update.reply──► Rubika
rubika_ai_response_loop (EP09) ──send_message──► Rubika
rubika_status_bot (EP11)
   ├─ Rubino like/comment
   └─ Rubino add_picture/add_video from RubikaContentSchedule
```

All three load clients via `load_rubika_user_client` **without** `evaluate_account_session_readiness`.

---

## Docker / runtime note

From `docker-compose.yml`:

| Service | Default mode | Profile |
|---------|--------------|---------|
| `rubika_worker` | inherits `.env` (`RUBIKA_DELIVERY_MODE` often `bot_api`); single `WORKER_ACCOUNT_ID` | always |
| `rubika_listener` | forced `user_account` | `rubika_listener` |
| `rubika_ai_loop` | forced `user_account` | `rubika_ai_loop` |
| `rubika_status_bot` | forced `user_account` | `rubika_status` |

There is **no** multi-account Rubika pool worker analogous to WhatsApp pool; user_account campaign sends still go through single-account `rubika_worker` then re-select inside EP03.

---

## Recommended Phase 2 enforcement points (priority)

1. **EP01** — central gate: resolve mode via `rubika_mode`, load account, `evaluate_account_session_readiness`, refuse with structured codes before any connector.
2. **EP03** — decide policy: honor `payload.account_id` (assigned) **or** document pool-only and stop writing campaign account_id as sender truth; always readiness-check the account that will send.
3. **EP05** — enqueue-time readiness skip for Rubika (prevents queue poison / wasted worker cycles).
4. **EP09 / EP10 / EP11** — share a `assert_rubika_user_client_ready(account_id)` before `load_rubika_user_client` / outbound.
5. **EP07** — align ops test with assigned-account send under user_account (or preflight pool pick).

Out of scope for message preflight: EP13/EP14 (auth), EP15 (unwired).

---

## Key file index

| File | Role |
|------|------|
| `workers/delivery.py` | Mode router |
| `workers/connectors/rubika.py` | Bot API HTTP transport |
| `workers/connectors/rubika_user.py` | rubpy transport + pool |
| `workers/rubika_account_pool.py` | Phase window + pool pick |
| `workers/rate_limit.py` | Cooldown / hourly (user path) |
| `workers/rubika_worker.py` | Redis consumer |
| `core_engine/services/operational_send.py` | Only readiness-gated send path |
| `core_engine/services/account_session_wiring.py` | Phase 1 readiness |
| `core_engine/services/rubika_mode.py` | Mode contract |
| `core_engine/services/queue_bridge.py` | Campaign enqueue |
| `workers/rubika_ai_response_loop.py` | AI outbound |
| `workers/rubika_group_listener.py` | Keyword outbound |
| `workers/rubika_status_bot.py` | Status/content outbound |
