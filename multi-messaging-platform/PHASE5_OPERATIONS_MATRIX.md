# PHASE5_OPERATIONS_MATRIX

| feature | backend | API | frontend | RBAC | audit | tested | status |
|---------|---------|-----|----------|------|-------|--------|--------|
| Protection Center page | Y | overview | tab مرکز حفاظت | read admin/op | n/a | Y | PASS |
| Account protection table | Y | overview | table | read | n/a | Y | PASS |
| Health display | Y | health/overview | badges | read | n/a | Y | PASS |
| Readiness vs send-now | Y | preflight codes | columns | read | n/a | Y | PASS |
| Lifecycle/quota | Y | overview | columns | read | n/a | Y | PASS |
| Circuit panel | Y | overview/health | panel + OPEN banner | read | n/a | Y | PASS |
| Circuit force-close | N (unsafe) | none | none | n/a | n/a | documented | PASS (intentionally absent) |
| Incidents list/filters | Y | /incidents | center | read | n/a | Y | PASS |
| Incident detail | Y | /incidents/{id} | detail pane | read | n/a | Y | PASS |
| Incident acknowledge | Y | POST ack | button | admin | Y | Y | PASS |
| Alerts + dedupe | Y | /alerts + sync | list/banner | read/ack admin | Y | Y | PASS |
| Secret redaction | Y | alerts | n/a | n/a | n/a | Y | PASS |
| Restore | Y | POST restore | confirm dialog | admin | Y | Y | PASS |
| Event timeline | Y | events/overview | table | read | source audit | Y | PASS |
| Campaign impact | Y | overview | cards | read | n/a | Y | PASS |
| Auto refresh | n/a | n/a | 30s | n/a | n/a | static | PASS |
| Migrations | NONE | — | — | — | — | alembic heads | PASS |
