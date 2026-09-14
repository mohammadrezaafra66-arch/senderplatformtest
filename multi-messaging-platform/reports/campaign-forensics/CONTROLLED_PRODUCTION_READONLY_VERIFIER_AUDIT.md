# Controlled Production Readonly Verifier — Source Audit

## Script
`scripts/_controlled_production_readonly_verifier.py`

## Classification gates

| Gate | Result | Evidence |
|------|--------|----------|
| NO_DB_WRITES | True | No INSERT/UPDATE/DELETE/text DML; only `query`/`count`/`scalar`/`execute(SET TRANSACTION READ ONLY)` |
| NO_SESSION_COMMIT | True | No `commit()`; `rollback()`+`close()` in finally |
| NO_SESSION_FLUSH_WITH_MUTATION | True | No `flush()`; no ORM field assignments |
| NO_CAMPAIGN_STATE_MUTATION | True | Campaign objects only read `.status`/`.id` |
| NO_MESSAGE_ATTEMPT_CREATION | True | MessageAttempt used only in `func.count` SELECT |
| NO_QUEUE_PUSH | True | No queue_bridge / lpush / rpush on production client |
| NO_REDIS_WRITE | True | `ReadOnlyRedisProxy` raises `RedisWriteBlockedError` on mutate cmds |
| NO_REDIS_DELETE | True | `delete`/`unlink` raise; pure quota patch never deletes |
| NO_REDIS_EXPIRE | True | expire/pexpire/getex raise |
| NO_EXTERNAL_HTTP_MUTATION | True | No httpx/requests/POST |
| NO_CAMPAIGN_START | True | Does not import/call `start_campaign` |
| NO_EXTERNAL_SEND | True | No send/deliver connectors |
| NO_SESSION_MUTATION | True | Does not call `store_channel_session` |
| NO_ACCOUNT_MUTATION | True | Account rows read-only via preflight SELECTs |
| NO_WORKER_MUTATION | True | `has_active_worker_coverage` uses Redis EXISTS only |
| NO_CONFIG_MUTATION | True | `controlled_production_enabled()` reads settings only |
| NO_DB_MIGRATION | True | No alembic/DDL |

## Call graph (side-effect review)

1. `controlled_production_enabled()` → settings read
2. `evaluate_campaign_send_preflight(db, id, redis=proxy)`
   - DB: SELECT Campaign/Account/Recipient/Staged/Rendered/windows
   - Redis via proxy: GET/TTL/EXISTS/SCARD/PING/LLEN only
   - `consume_circuit_probe=False` on account preflight (no probe consume)
   - `read_quota_snapshot` **patched** to `_pure_read_quota_snapshot` (no DEL)
   - `log_campaign_safety_event` → logger only
3. Sentinel SELECTs before/after

## Redis fail-closed proof
Commands blocked with raise: SET/SETEX/DEL/UNLINK/EXPIRE/INCR/HSET/LPUSH/RPUSH/SADD/ZADD/FLUSHDB/FLUSHALL/MULTI/PIPELINE/…

## Prior unsafe script
`_controlled_production_spotcheck.py` → **MUTATING/UNPROVEN** (calls production `read_quota_snapshot` which may `redis.delete`). **Do not execute.**
