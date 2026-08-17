# 08 ROLLBACK PLAN

Before any live send (and available now):

1. Set `REAL_MESSAGE_SENDING_ENABLED=false` (canonical kill switch) and restart workers/API so settings reload.
2. Pause the pilot campaign (`POST /campaigns/{id}/stop`) — DB PAUSED + Redis pause.
3. Verify queue bridge claims nothing for PAUSED campaigns (Phase 6).
4. Leave queued/staged rows intact.
5. Inspect Protection Center incidents / circuit / health.
6. Do not delete RenderedMessage / MessageAttempt / logs.
7. Restore accounts only through validated recovery (`REQUIRES_LOGIN` / quarantine restore APIs).

No destructive cleanup.

Kill switch verified in tests: OFF → connectors not called.
Campaign pause verified in Phase 6 tests.
