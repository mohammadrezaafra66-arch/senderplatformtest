# L4 Rehearsal Hardening Audit (source only — not executed)

**Date:** 2026-08-31  
**Script:** `scripts/_l4_migration_rehearsal.py`  
**Rehearsal executed:** **NO**  
**Alembic run:** **NO**  
**Production schema touched:** **NO**

## Guards

| Guard | Status |
|---|---|
| `assert_rehearsal_db_name` | Exact allowlist: `mmp_l4_rehearsal`, `mmp_l4_rehearsal_downgrade` |
| Production denylist | `mmp_db`, `postgres`, `template0`, `template1` |
| Used by | `_drop_create`, `_restore`, `_alembic`, `_counts`, inventory helpers, `reset_stale_rehearsal_databases`, `_inspect_canonical_loader` |
| `assert_database_url_is_rehearsal` | Parses URL path before every alembic/inspect run |
| `rehearsal_database_url` | Built from constants — does **not** inherit host/`mmp_core_api` `DATABASE_URL` |

## Alembic runner

| Property | Value |
|---|---|
| Name | `mmp_l4_alembic_runner` |
| Mode | `docker run --rm` (one-shot, removed after command) |
| Network | `multi-messaging-platform_default` (reach postgres only) |
| Mount | `{PROJECT_ROOT}:/app:ro` — latest host alembic revisions |
| Target URL | `postgresql://…@postgres:5432/{allowlisted_db}` only |
| Uses `mmp_core_api` as runner | **NO** |
| `docker cp` into `mmp_core_api` | **NO** |
| Redis / workers / send flags | Forced off / empty |

Image discovery may read `mmp_core_api` image name for dependency parity (`docker inspect`), but alembic never executes inside that container. Override with `L4_ALEMBIC_RUNNER_IMAGE`.

## Stale reset

`reset_stale_rehearsal_databases()` DROP+CREATE **only** the two allowlisted DBs before restore.

## Production source

`pg_dump` of `mmp_db` only. Metadata records path, SHA256, timestamp. Restore targets allowlisted DBs only.
