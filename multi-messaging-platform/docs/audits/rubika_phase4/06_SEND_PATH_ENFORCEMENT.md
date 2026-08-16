# Send Path Enforcement (Phase 4)

All Phase 2/3 production entry points call central preflight.

Layer P0 (all modes/contexts):

1. Circuit assert (OPEN deny / HALF_OPEN probe)
2. Quarantine check

Then existing readiness + policy layers.

| Path | Circuit | Quarantine | Evidence |
|------|---------|------------|----------|
| bot_api | yes | yes | deliver_rubika_live |
| user_account | yes | yes | deliver_rubika_user_live |
| side AI/listener/status | yes | yes | require_rubika_side_channel_send |
| ops test | yes (via connector) | yes | operational_send → connector |
| campaign/worker | yes | yes | queue → connector |

Production bypass count remains **0**.
