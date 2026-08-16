# Enforcement Map (post-implementation)

| ID | Entry | Gate | Status |
|----|-------|------|--------|
| EP02 | `deliver_rubika_live` | `evaluate_rubika_send_preflight` before HTTP | CLOSED |
| EP03 | `deliver_rubika_user_live` | preflight on **assigned** account_id before rubpy send | CLOSED |
| EP01/EP04 | delivery / rubika_worker | via EP02/03 | CLOSED |
| EP05/EP06 | queue / campaign | assignment respected; worker preflight | CLOSED |
| EP07 | ops test send | readiness + connector preflight | CLOSED |
| EP09 | AI loop | `require_rubika_side_channel_send` before send | CLOSED |
| EP10 | group listener reply | side_channel preflight before reply | CLOSED |
| EP11 | status bot | side_channel preflight before like/publish | CLOSED |
| EP12/EP14 | scripts | non-prod; documented residual | residual |
