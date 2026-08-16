# Phase 3 Send Path Matrix

| ID | Entry point | Production? | Assigned account source | Phase2 readiness | Phase3 policy | Reservation | Transport | Bypass? | Evidence |
|----|-------------|-------------|-------------------------|------------------|---------------|-------------|-----------|---------|----------|
| EP01 | `deliver_platform_message` | yes | payload | via EP02/03 | via EP02/03 | via EP03 | via EP02/03 | no | delivery.py routes only |
| EP02 | `deliver_rubika_live` | yes | `payload.account_id` | preflight A–E | state/session (bot_api; no warm-up F) | n/a | Bot API HTTP | no* | rubika.py |
| EP03 | `deliver_rubika_user_live` | yes | `payload.account_id` | preflight A–G | full Layer F | Lua reserve | rubpy | no | rubika_user.py |
| EP04 | `rubika_worker.send_message` | yes | queue payload | via EP01 | via EP01 | via EP03 | via EP01 | no | worker |
| EP05 | queue bridge | yes | Message.account_id | worker | worker | worker | worker | no | queue_bridge |
| EP06 | campaign assign | yes | prepare-time | worker | worker | worker | worker | no | assignment |
| EP07 | ops test send | yes | selected account | readiness + connector | connector | connector | connector | no | operational_send |
| EP08 | ops preflight API | n/a | selected | readiness | n/a (no transport) | n/a | none | no | operational_send |
| EP09 | AI response loop | yes | selected pool acct | `require_rubika_side_channel_send` | Layer F on | soft (no reserve)** | rubpy | no | ai loop |
| EP10 | group listener reply | yes | selected | side_channel preflight | Layer F on | soft** | reply | no | listener |
| EP11 | status bot | yes | status account | side_channel preflight | Layer F on | soft** | like/publish | no | status bot |
| EP12 | `scripts/rubika_user_send_test.py` | **no** | CLI | none | none | none | yes | residual non-prod | documented |
| EP13 | OTP login | n/a | account | mode gates | n/a | n/a | auth only | no | no message send |
| EP14 | GUID debug script | **no** | CLI | none | none | none | resolve only | residual non-prod | documented |
| EP15 | Celery `send_message_task` | no live Rubika | n/a | n/a | n/a | n/a | none | no | unwired |

\*bot_api intentionally omits user-account warm-up/daily application policy; identity/session/state gates remain. Not a silent sender replace.

\*\*Side channels enforce preflight policy (deny before transport) but do not yet perform Lua reservation; campaign/user worker path does. Residual gap tracked in `07_REMAINING_GAPS.md`.

## Acceptance

- Production **silent sender replace** bypasses: **0**
- Production paths missing Phase 2 readiness gate: **0**
- Production user_account campaign/worker path missing Phase 3 policy: **0**
