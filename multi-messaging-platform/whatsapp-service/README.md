# WhatsApp Service (Baileys)

Node.js microservice for WhatsApp delivery via [@whiskeysockets/baileys](https://github.com/WhiskeySockets/Baileys). Replaces Playwright/WhatsApp Web for production scale (50 accounts × ~200 msgs/day).

## Architecture (Phase 3 — Redis-only bridge)

```
FastAPI / Celery (Python)
    │ RPUSH JSON → whatsapp:raw_outgoing
    ▼
Node rawOutgoingBridge (BLPOP loop in worker.js)
    │ queue.add() → BullMQ whatsapp_outgoing
    ▼
Baileys worker (send + human behavior)
    │ LPUSH → whatsapp:results
    │ LPUSH → whatsapp:session_status (on 401)
    ▼
Celery Beat (every 10s)
    ├─ bulk insert audit_logs ← whatsapp:results
    └─ mark disconnected      ← whatsapp:session_status
```

**No Node subprocess from Python** — all cross-language traffic is Redis lists only.

## Phase 4 — Admin API + PM2 + Docker

### Admin API (`server.js`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness |
| GET | `/api/status/:accountId` | `{ accountId, linked }` from `sessions/{id}/creds.json` |
| POST | `/api/link-session` | Body `{ accountId }` → `{ accountId, qrCodeBase64 }` |
| POST | `/api/warmup` | Schedule cross-warmup matrix → `{ success, pairedAccounts, totalJobs }` |

Optional header `X-Api-Key` when `API_KEY` is set.

```bash
npm run api
# or via PM2: wpp-api in ecosystem.config.cjs
```

### PM2 (50 lines → 5 shards)

```bash
export WPP1_ACCOUNT_IDS=98901...,98902...
# ... WPP2 .. WPP5
pm2-runtime start ecosystem.config.cjs
```

### Docker

```bash
docker build -t whatsapp-service ./whatsapp-service
docker run -p 3000:3000 \
  -e REDIS_URL=redis://host.docker.internal:6379/0 \
  -e WPP1_ACCOUNT_IDS=989048249523 \
  -v $(pwd)/sessions:/app/sessions \
  -v $(pwd)/config/proxies.json:/app/config/proxies.json:ro \
  whatsapp-service
```

## Prerequisites

- Node.js ≥ 20
- PM2 (`npm i -g pm2`)
- Redis (same instance as `multi-messaging-platform`)
- Dedicated proxy per account (`config/proxies.json`)

## Quick start

```bash
cd multi-messaging-platform/whatsapp-service
cp .env.example .env
cp config/proxies.json.example config/proxies.json
# Edit .env: REDIS_URL, ACCOUNT_IDS, WORKER_ID
# Edit config/proxies.json: map each accountId → proxy
npm install
```

### Single worker (dev)

```bash
# .env
ACCOUNT_IDS=989048249523
WORKER_ID=1

node worker.js
```

### PM2 cluster (5 shards × 10 accounts)

```bash
# Set shard account lists before start:
export WPP1_ACCOUNT_IDS=98901...,98902...
export WPP2_ACCOUNT_IDS=...
# … WPP5_ACCOUNT_IDS

pm2 start ecosystem.config.js
pm2 logs
```

## Python backend integration

In `.env` (FastAPI / Celery):

```env
WHATSAPP_DELIVERY_MODE=baileys
REAL_QUEUE_PUSH_ENABLED=true
REAL_MESSAGE_SENDING_ENABLED=true
```

- **Campaign sends:** `queue_bridge.py` RPUSHes to `whatsapp:raw_outgoing` when mode is `baileys`.
- **UI test send:** `operational_send.py` → same raw list.
- **Results:** Celery `consume_whatsapp_baileys_results` (10s) bulk-writes `audit_logs`.
- **Session 401:** Celery `consume_whatsapp_baileys_session_status` → `channel_sessions` unlinked + `requires_login`.

Kill switch (both keys synced):

- `system:whatsapp_send_disabled` (existing)
- `whatsapp:kill_switch` (Baileys workers)

```powershell
.\scripts\whatsapp_send_kill_switch.ps1 -Enable
```

## Session linking

```bash
# 1. Map proxy for this account in config/proxies.json
# 2. Link via QR (isolated CLI — exits after success)
node link-session.js 989048249523
# or: npm run link -- 989048249523
```

Steps performed by `link-session.js`:
1. Resolves dedicated proxy via `getProxyForAccount`
2. Health-checks proxy (aborts if missing/unhealthy — no direct IP)
3. Prints QR in terminal (`printQRInTerminal: true`)
4. Saves creds to `sessions/{accountId}/` via `useMultiFileAuthState`
5. On `connection.open`: waits 3s, closes socket, exits 0

On **401 / logged out:** remove session folder and retry.

Creds backup every 24h in worker mode → `sessions/{phone}/backups/`.

## Warmup matrix (Phase 5 — Cross-Warmup)

Auto-discovers linked lines from `sessions/*/creds.json` (no env account lists).

```bash
# Requires ≥2 linked sessions + worker PM2 running
npm run warmup

# Or via Admin API (server stays alive — no queue.close / redis.quit per request)
curl -X POST http://localhost:3000/api/warmup -H "X-Api-Key: $API_KEY"
```

- Shuffles accounts → pairs (odd count → one trio + pairs)
- 4-message conversation per group (A↔B or trio round-robin)
- Jobs go **directly** to BullMQ `whatsapp_outgoing` with `delay` (10–180 min window)
- `route: "warmup"`, `delayAfter: 0`

## Phase 6 — Alerting & resilience (Production)

### Session invalid (401) webhook

When a session is invalidated, the worker publishes to `whatsapp:session_status` **and** POSTs to:

- `TELEGRAM_WEBHOOK_URL` (preferred), or
- `ADMIN_ALERT_WEBHOOK`

```json
{
  "text": "خطا: سشن واتس‌اپ برای شماره {accountId} باطل شده است...",
  "accountId": "989048249523",
  "type": "session_invalid"
}
```

### Stalled jobs (BullMQ)

Worker sets `maxStalledCount: 1` — if a job stalls (proxy crash / worker restart mid-send), BullMQ re-queues it once before failing.

### Graceful shutdown (PM2 / Docker restart)

`SIGINT` / `SIGTERM` handlers in `worker.js` and `server.js`:

1. Stop BullMQ worker / HTTP server
2. Close Baileys sockets (`sock.end()`)
3. `process.exit(0)`

## Job format (BullMQ)

```json
{
  "jobId": "unique-id",
  "accountId": "989048249523",
  "jid": "989122270261@s.whatsapp.net",
  "text": "متن پیام",
  "typingSeconds": 4.2,
  "delayAfter": 75000,
  "route": "campaign"
}
```

## Proxy config (`config/proxies.json`)

Copy `config/proxies.json.example` → `config/proxies.json`.

Mapping priority:

1. `accountId` — exact match (E.164 digits, e.g. `989048249523`)
2. `workerId` — if exactly one proxy row has this worker shard id
3. `default: true` — fallback proxy for unmapped accounts
4. `ALLOW_NO_PROXY_FALLBACK=true` — direct IP (emergency only, default off)

Reload without restart: `pm2 sendSignal SIGHUP wpp-worker-1` (or copy updated `proxies.json` and restart).

Path resolves from **whatsapp-service root** (not cwd): `config/proxies.json` or `PROXIES_CONFIG` env.

## Proxy golden rule

If proxy is missing or unhealthy → socket is **not** created. Never fall back to direct IP.

## Files

| File | Role |
|------|------|
| `worker.js` | BullMQ consumer entrypoint |
| `sessionManager.js` | Auth state, socket lifecycle |
| `proxyManager.js` | Proxy map + health check |
| `behavioralEngine.js` | Typing, jitter, siesta, long breaks |
| `messageProcessor.js` | Job handler + results |
| `warmup.js` | Cross-account warmup |
| `alertWebhook.js` | 401 / session-invalid HTTP alerts |
| `enqueueJob.js` | CLI for Python enqueue |
| `ecosystem.config.js` | PM2 5-shard config |

## Phase 2 — live test (one message)

```bash
# 1. Link session (phase 1)
node link-session.js 989048249523

# 2. Kill switch OFF
docker exec mmp_redis redis-cli SET whatsapp:kill_switch false

# 3. Terminal A — worker
node worker.js

# 4. Terminal B — inject test job
npm run test-send
# or with env:
TEST_ACCOUNT_ID=989048249523 TEST_RECIPIENT=989122270261 TEST_TEXT="پیام تست" npm run test-send

# 5. Check result
docker exec mmp_redis redis-cli LRANGE whatsapp:results 0 0
```


1. One account + one PM2 worker + kill switch OFF
2. Verify `whatsapp:results` → audit_logs
3. Enable warmup 3–4 days
4. Scale to 5 PM2 shards / 50 accounts gradually

## Playwright legacy

Keep `WHATSAPP_DELIVERY_MODE=web` to use existing Windows/Docker Playwright pool. Switch to `baileys` when Node service is ready.
