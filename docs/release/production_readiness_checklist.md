# Production Readiness Checklist

**Project:** senderplatformtest / multi-messaging-platform  
**Phase:** 6 complete (testing & hardening)  
**Date:** 2026-06-17

> **Phase 6 is test and hardening complete, but production deployment still requires environment-specific review.**

## Completed in Phase 6

- [x] Routine test suite passes (`pytest` — 52 tests)
- [x] Stress tests implemented and passing locally (`pytest -m stress`)
- [x] Chaos tests implemented and passing locally (`pytest -m chaos`)
- [x] Alembic migrations present under `alembic/versions/` (including opt-events, users, audit_logs)
- [x] `.env.example` documents required variables with placeholders
- [x] No real secrets committed (`.env` gitignored)
- [x] `SESSION_SECRET` required for session encryption (validated in `config.py`)
- [x] Audit log service and `GET /audit/logs` (admin only)
- [x] RBAC via JWT roles (`admin`, `operator`, `viewer`)
- [x] Consent gate (opt-in / opt-out / blacklist) on enqueue and dispatch
- [x] Dry-run and shadow mode tested
- [x] Prometheus KPI metrics at `GET /metrics`
- [x] GitHub Actions CI workflow (`.github/workflows/tests.yml`)
- [x] Release evidence document (`docs/release/phase6_release_evidence.md`)

## Still required before production

- [ ] **Rollback plan** — document and rehearse rollback for API, workers, and DB migrations
- [ ] **Production secret manager** — store `SECRET_KEY`, `SESSION_SECRET`, DB/Redis URLs, channel credentials outside git
- [ ] **Real user authentication** — replace or sync `fake_users_db` with DB `User` model and hashed passwords
- [ ] **Deployment review** — hosting, TLS, firewall, Redis/Postgres HA, Celery worker scaling
- [ ] **Migration runbook** — `alembic upgrade head` on staging before production
- [ ] **Monitoring alerts** — wire Prometheus/Grafana alerts to on-call
- [ ] **Backup & restore** — Postgres and Redis persistence strategy
- [ ] **Rate limits & channel policies** — validate against real platform limits
- [ ] **Legal / compliance** — opt-in/opt-out process for production contacts

## Pre-deploy smoke checklist

1. `pytest -m "not stress and not chaos"` green
2. `docker compose up` with valid `.env` and `SESSION_SECRET`
3. `GET /health` → 200
4. `GET /metrics` → 200 with KPI counters
5. `alembic upgrade head` on target database
6. Login with test JWT user; verify RBAC on admin endpoint
7. Dry-run send does not hit real channels

## Sign-off

| Role | Name | Date | Notes |
|------|------|------|-------|
| Engineering | | | |
| Operations | | | |
| Security | | | |
