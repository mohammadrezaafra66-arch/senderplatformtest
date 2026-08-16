# Operator Actions

| Action | Endpoint | Role | Audit |
|--------|----------|------|-------|
| Refresh overview | GET overview | admin/operator | n/a |
| Restore quarantined account | POST `/rubika/accounts/{id}/restore` | admin | `rubika_account_restore` |
| Acknowledge incident | POST `/rubika/incidents/{id}/acknowledge` | admin | `rubika_incident_acknowledge` |
| Acknowledge alert | POST `/rubika/alerts/{dedupe}/acknowledge` | admin | `rubika_alert_acknowledge` |

## Not implemented
**Force Close circuit** — unsafe without half-open probe evidence; would weaken Phase 4 fail-closed semantics.

## Restore UI gates
- BANNED → blocked
- REQUIRES_LOGIN → login flow (button disabled)
- SESSION_NOT_READY → login required
- QUARANTINED + session ready → confirm dialog then backend restore
