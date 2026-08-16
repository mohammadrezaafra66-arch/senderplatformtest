# Rubika Phase 5 — Operations Baseline

## START_HEAD
`f5169e9b05adbe1b2b89bbc34bf70aa252834128` (Phase 4 complete)

## UI / API classification (pre-Phase 5)

| Area | Classification | Notes |
|------|----------------|-------|
| Rubika account pool | WORKING | `/rubika` tab pool + pool APIs |
| Groups | WORKING | group CRUD + messages |
| Send logs | WORKING | send-log panel |
| Assistant | WORKING | pricing cache debug |
| Content/status schedule | WORKING | status tab |
| Account connection/readiness | PARTIAL | login panel exists; not linked to protection view |
| Account errors | PARTIAL | pool last_error fields only |
| Health APIs | BACKEND_EXISTS_UI_MISSING | GET `/rubika/health`, account health |
| Protection APIs | BACKEND_EXISTS_UI_MISSING | GET `/rubika/protection/status` |
| Incidents | BACKEND_EXISTS_UI_MISSING | GET `/rubika/incidents` |
| Restore (quarantine) | BACKEND_EXISTS_UI_MISSING | POST `/rubika/accounts/{id}/restore` (admin) |
| Pool restore | WORKING | POST `/pool/restore` — different semantics |
| Circuit state | BACKEND_EXISTS_UI_MISSING | embedded in health/status |
| Alerts | BACKEND_MISSING | Phase 5 |
| Protection overview aggregate | BACKEND_MISSING | Phase 5 |
| Event timeline | BACKEND_MISSING / PARTIAL | AuditLog exists; no protection timeline UI |

## Decision
Add Protection Center tab under `/rubika` ("مرکز حفاظت"), aggregate `GET /rubika/protection/overview`, Redis-backed alerts with dedupe, no schema migration.
