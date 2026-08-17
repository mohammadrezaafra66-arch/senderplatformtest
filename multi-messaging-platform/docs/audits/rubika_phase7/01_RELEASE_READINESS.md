# 01 RELEASE READINESS

See `00_RELEASE_READINESS_BASELINE.md`.

This cloud agent environment:

- Redis reachable
- Postgres reachable (disposable test databases)
- No Docker worker/API containers running
- No duplicate Rubika sender process observed
- Kill switches default/env: `REAL_MESSAGE_SENDING_ENABLED=false`, `CHANNEL_CONNECTORS_ENABLED=false`, `REAL_QUEUE_PUSH_ENABLED=false`

API liveness is `/health`. Worker starts via `python -m workers.run_forever` with `WORKER_PLATFORM=rubika`. Fatal missing `SESSION_SECRET` is enforced at Settings validation.

Frontend (verified in Phase 6 and unchanged structurally): campaign GPT, products, preview, committed render, message logs, capacity/preflight, Rubika Protection Center.
