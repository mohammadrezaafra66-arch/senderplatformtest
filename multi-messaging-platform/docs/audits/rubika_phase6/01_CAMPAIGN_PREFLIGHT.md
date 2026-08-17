# Campaign preflight

Service: `core_engine.services.campaign_preflight.evaluate_campaign_send_preflight`

Endpoint: `GET /campaigns/{id}/preflight` (ADMIN/OPERATOR/VIEWER, read-only)

Start: `start_campaign` calls preflight **server-side** for Rubika before RUNNING/push.

Codes: `CAMPAIGN_READY`, `CAMPAIGN_NOT_PREPARED`, `CAMPAIGN_NO_MESSAGES`,
`CAMPAIGN_NO_SENDERS`, `CAMPAIGN_SENDER_BLOCKED`, `CAMPAIGN_INSUFFICIENT_CAPACITY`,
`CAMPAIGN_OUTSIDE_SEND_WINDOW`, `CAMPAIGN_CIRCUIT_OPEN`, `CAMPAIGN_ALL_ACCOUNTS_QUARANTINED`,
`CAMPAIGN_CONFIGURATION_INVALID`, `CAMPAIGN_CAPACITY_UNKNOWN`, `CAMPAIGN_ALREADY_RUNNING`,
`CAMPAIGN_DEPENDENCY_ERROR`.

Fail closed: Redis unevaluable → `CAMPAIGN_CAPACITY_UNKNOWN` (start HTTP 503).
DB operational errors → `CAMPAIGN_DEPENDENCY_ERROR` (no status mutation).

Circuit OPEN → `allowed_to_start=false`, no queue push.

Same-day shortfall is a **warning** (multi-day OK), not a hard block.

Derived execution states (not a DB enum): READY, CAPACITY_WARNING, BLOCKED, RUNNING,
PAUSED_SAFETY, PAUSED_OPERATOR, WAITING_WINDOW, WAITING_CAPACITY, COMPLETED, FAILED,
READY_TO_RESUME.

Mapping: `Campaign.status` remains draft/prepared/running/paused/…. Safety state is
computed from status + circuit + pause Redis + capacity snapshots.

Does **not** consume quota. Does **not** reassign senders.
