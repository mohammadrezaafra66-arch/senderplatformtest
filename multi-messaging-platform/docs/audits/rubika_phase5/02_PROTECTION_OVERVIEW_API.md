# Protection Overview API

## Endpoint
`GET /rubika/protection/overview`  
Roles: admin, operator

## Response shape
- `system.circuit` — state, opened_at, open_until, reason, probe budget, systemic counts
- `summary` — totals for ready/healthy/degraded/throttled/quarantined/requires_login/incidents/alerts/circuit
- `accounts[]` — operational row (no secrets)
- `incidents[]` — open incidents
- `alerts[]` — unresolved alerts (synced from evidence)
- `campaign_impact` — evidence-backed counts only
- `events[]` — audit + incident timeline
- `operator_notes.circuit_force_close=false` + reason

## Related
- `GET /rubika/accounts/{id}/protection`
- `GET /rubika/protection/events`
- `GET /rubika/alerts`
- Existing Phase 4 health/incidents/restore endpoints retained
