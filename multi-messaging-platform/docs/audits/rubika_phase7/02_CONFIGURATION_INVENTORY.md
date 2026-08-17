# 02 CONFIGURATION INVENTORY

Values are **never** recorded. `present` means a non-empty, non-placeholder env value in this agent process.

| SETTING NAME | required? | present? | source category | safe validation result |
|---|---|---|---|---|
| SESSION_SECRET | yes (API) | yes | env | Fernet-length secret present |
| SECRET_KEY | yes (API) | yes | env | set |
| DATABASE_URL | yes | yes | env | postgres host=localhost, db=mmp_phase6_test (disposable) |
| REDIS_URL | yes | yes | env | redis host=localhost, no password |
| RUBIKA_DELIVERY_MODE | yes | yes | env | `bot_api` |
| RUBIKA_USER_ACCOUNT_ENABLED | yes for user_account | yes | env | FALSE |
| RUBIKA_HOURLY_SEND_CAP | policy | no | WorkerSettings default 50 | default applies |
| RUBIKA_DAILY_SEND_CAP | policy | no | default 100 | default applies |
| RUBIKA_MIN/MAX_SEND_DELAY_SECONDS | policy | no | defaults 5/15 | default applies |
| circuit/health settings | policy | no | WorkerSettings defaults | default applies |
| CAMPAIGN_DISPATCH_* / RUBIKA_MAX_IN_FLIGHT_* | scale | no | Phase 6 defaults | default applies |
| AFRAKALA_PRODUCT_API_BASE_URL | for product pilot | no | unset | CONFIG_PENDING |
| AFRAKALA_PRODUCT_API_TOKEN | if URL requires auth | no | unset | CONFIG_PENDING |
| AFRAKALA_PRODUCT_API_TIMEOUT_SECONDS | if live fetch | no | default 8 | default |
| AFRAKALA_PRODUCT_MAX_STALENESS_SECONDS | if live fetch | no | default 300 | default |
| AFRAKALA_PRODUCT_PRICE_CURRENCY | if live fetch | no | default IRR | **empirically UNVERIFIED** |
| AFRAKALA_PRODUCT_PRICE_DISPLAY_UNIT | if live fetch | no | default ریال | **empirically UNVERIFIED** |
| OPENAI_API_KEY | for GPT pilot | no | unset | CONFIG_PENDING |
| OPENAI_MODEL | for GPT pilot | no | default gpt-4o-mini | default only |
| OPENAI_TIMEOUT_SECONDS | for GPT pilot | no | default 30 | default |
| OPENAI_VARIATION_COUNT | for GPT pilot | no | default 5 | default |
| OPENAI_MAX_OUTPUT_TOKENS | for GPT pilot | no | default 1200 | default |
| REAL_QUEUE_PUSH_ENABLED | live path | yes | env | FALSE |
| REAL_MESSAGE_SENDING_ENABLED | **kill switch** | yes | env | FALSE |
| CHANNEL_CONNECTORS_ENABLED | live path | yes | env | FALSE |
| WORKER_EXECUTION_ENABLED | Phase 4 debug | no | default false | not a worker transport gate |
| DRY_RUN | worker router | no | default false | unset |
| SHADOW_MODE | worker router | no | default false | unset |
| OPS_LIVE_SEND_API_ENABLED | API test send | no | default false | FALSE |

No `.env` file in the repository workspace.
