# RBAC Audit

## Existing pattern
`requires_role(RoleType.…)` + frontend `canManageRubika` (admin only for mutating Rubika UI).

## Phase 5 mapping
| Capability | Backend | Frontend |
|------------|---------|----------|
| Read overview/health/incidents/alerts | admin, operator | any authenticated Rubika page user (operators see read-only actions) |
| Restore | admin only | `canManage` |
| Incident/alert acknowledge | admin only | `canManage` |
| Circuit force-close | not exposed | not exposed |

## Tests
`tests/api/test_rubika_phase5.py` — operator denied restore (403); admin can call restore path (banned → 400 BANNED).
