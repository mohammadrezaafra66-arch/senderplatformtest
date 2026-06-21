# Deployment Guide — Multi-Messaging Platform

Operational checklist for running Sender Platform with **live WhatsApp Web** delivery (Phase 8-WA-Web + Phase 9).

---

## Architecture (WhatsApp Web)

```
UI (3010) → core_api (8001) → Postgres / Redis
                                    ↓
                         queue_bridge → queue:whatsapp:{account_id}
                                    ↓
                    whatsapp_worker_pool (Playwright + mounted profile)
```

The WhatsApp browser session is linked **on the Windows host** (`whatsapp_web_link_local.ps1`).  
Docker workers read the same files via:

```yaml
volumes:
  - ./storage/browser_profiles:/app/storage/browser_profiles
```

Profile path for account `248`:

```text
storage/browser_profiles/whatsapp/account-248/
```

---

## Pre-flight (every deploy)

### 1. Infrastructure

```powershell
cd multi-messaging-platform
docker compose up -d postgres redis core_api whatsapp_worker_pool
```

Verify:

| Check | Command |
|--------|---------|
| API health | `curl http://localhost:8001/docs` |
| Redis | `docker compose exec redis redis-cli ping` → `PONG` |
| Pool worker | `docker compose ps whatsapp_worker_pool` → Running |
| Profile mount | `dir storage\browser_profiles\whatsapp\account-248` (must contain Chromium data) |

### 2. Link WhatsApp (one-time per account)

On **Windows** (QR visible):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\whatsapp_web_link_local.ps1 -AccountId 248
```

Then in UI: **Accounts → WhatsApp Web → Register connection**.

### 3. Environment variables (`.env`)

Copy `.env.example` → `.env` and set:

#### Required secrets

| Variable | Description |
|----------|-------------|
| `SESSION_SECRET` | Fernet key for encrypted channel sessions |
| `SECRET_KEY` | App secret |

#### Live delivery gates (all must be `true` / `false` as shown)

| Variable | Production value | Purpose |
|----------|------------------|---------|
| `REAL_QUEUE_PUSH_ENABLED` | `true` | Allow `queue_bridge` to push to Redis |
| `REAL_MESSAGE_SENDING_ENABLED` | `true` | Workers send real messages |
| `CHANNEL_CONNECTORS_ENABLED` | `true` | Enable platform connectors |
| `DRY_RUN` | `false` | Disable dry-run in workers |
| `WORKER_EXECUTION_ENABLED` | `true` | Workers process queues |

#### WhatsApp Web pool

| Variable | Example | Purpose |
|----------|---------|---------|
| `WHATSAPP_DELIVERY_MODE` | `web` | Use Playwright (not Cloud API) |
| `WHATSAPP_ACCOUNT_IDS` | `248` | Accounts this pool replica serves |
| `WORKER_POOL_SIZE` | `1` | Match `docker compose --scale whatsapp_worker_pool=N` |
| `WHATSAPP_WEB_PROFILE_ROOT` | `storage/browser_profiles/whatsapp` | Profile root inside container |
| `WHATSAPP_WEB_HEADLESS` | `true` | Headless Chromium in Docker |
| `REAL_QUEUE_PUSH_ENABLED` | `true` | Required for campaign / E2E queue push |

#### Phase 9.2 API live test (optional — UI “ارسال واقعی”)

| Variable | Value |
|----------|-------|
| `OPS_LIVE_SEND_API_ENABLED` | `true` |

#### Host URLs (Docker Compose)

| Variable | Inside containers |
|----------|-------------------|
| `DATABASE_URL` | `postgresql://mmp_user:mmp_pass@postgres:5432/mmp_db` |
| `REDIS_URL` | `redis://redis:6379/0` |

When running scripts **on the Windows host**, use `localhost` instead of `postgres` / `redis`.

After `.env` changes:

```powershell
docker compose restart core_api whatsapp_worker_pool
```

---

## Production Checklist

Use this list before enabling live sends in production.

### Security & secrets

- [ ] `SESSION_SECRET` is a valid Fernet key (not the example placeholder)
- [ ] `SECRET_KEY` changed from default
- [ ] `.env` is **not** committed to git
- [ ] Admin password changed from `admin123`

### Safety gates

- [ ] `REAL_QUEUE_PUSH_ENABLED=true` only when campaigns should enqueue
- [ ] `REAL_MESSAGE_SENDING_ENABLED=true`
- [ ] `CHANNEL_CONNECTORS_ENABLED=true`
- [ ] `DRY_RUN=false`
- [ ] `OPS_LIVE_SEND_API_ENABLED` — set `true` only if API live test sends are intended

### WhatsApp Web

- [ ] Account linked on Windows; profile exists at `storage/browser_profiles/whatsapp/account-{id}/`
- [ ] UI shows **متصل** for the account
- [ ] `WHATSAPP_ACCOUNT_IDS` lists all active WhatsApp account IDs (e.g. `248`)
- [ ] `WORKER_POOL_SIZE` matches number of `whatsapp_worker_pool` replicas
- [ ] `whatsapp_worker_pool` volume mount includes `./storage/browser_profiles`
- [ ] `WHATSAPP_DELIVERY_MODE=web`

### Workers & Redis

- [ ] `mmp_redis` running; workers use `REDIS_URL=redis://redis:6379/0`
- [ ] `whatsapp_worker_pool` heartbeat visible (UI → Accounts → WhatsApp Web → pool status)
- [ ] Only **one** ACTIVE WhatsApp account per round-robin partition, **or** `WHATSAPP_ACCOUNT_IDS` scoped correctly

### Frontend

- [ ] `frontend/.env.local` → `NEXT_PUBLIC_API_URL=/backend`
- [ ] UI at `http://localhost:3010` (not port 3000)

### Smoke test (Phase 9.1 E2E)

```powershell
# On Windows host:
$env:DATABASE_URL = "postgresql://mmp_user:mmp_pass@localhost:5432/mmp_db"
$env:REDIS_URL = "redis://localhost:6379/0"
python scripts/e2e_whatsapp_real_send.py --account-id 248 --recipient 09XXXXXXXXX
```

Expected: green `ارسال با موفقیت انجام شد` and message on the recipient phone.

Or inside Docker:

```powershell
docker compose run --rm core_api python scripts/e2e_whatsapp_real_send.py --account-id 248 --recipient 09XXXXXXXXX
```

### Rollback (disable live sends)

```env
REAL_QUEUE_PUSH_ENABLED=false
REAL_MESSAGE_SENDING_ENABLED=false
CHANNEL_CONNECTORS_ENABLED=false
DRY_RUN=true
OPS_LIVE_SEND_API_ENABLED=false
```

```powershell
docker compose restart core_api whatsapp_worker_pool bale_worker telegram_worker rubika_worker
```

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `ModuleNotFoundError: playwright` in Docker | Rebuild image: `docker compose build core_api` |
| `whatsapp_web_profile_missing` | Profile not mounted or not linked on host |
| `No pool heartbeat` | `whatsapp_worker_pool` not running or Redis unreachable |
| `queue_bridge pushed 0` | `REAL_QUEUE_PUSH_ENABLED=false` or campaign not `RUNNING` |
| QR works on Windows but send fails in Docker | Check `storage/browser_profiles/whatsapp/account-248` exists and pool has volume mount |
| Message stuck — no `MessageAttempt` | Worker not assigned to account ID in `WHATSAPP_ACCOUNT_IDS` |

---

## Related scripts

| Script | Purpose |
|--------|---------|
| `scripts/whatsapp_web_link_local.ps1` | Link WhatsApp via QR on Windows |
| `scripts/e2e_whatsapp_real_send.py` | Phase 9.1 E2E: DB → queue_bridge → worker → monitor |
| `scripts/phase9_e2e_operational_verify.ps1` | API dry-run operational verify |
| `scripts/phase9_2_live_send_preflight_verify.ps1` | Live send preflight API verify |
