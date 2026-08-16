# Preflight Contract

**Service:** `core_engine/services/rubika_preflight.py`  
**Function:** `evaluate_rubika_send_preflight`

## Result

`RubikaPreflightResult(allowed, code, message, account_id, delivery_mode, session_type, retryable, details)`

Worker mapping: `preflight_to_worker_result` → `error_code=rubika_<code.lower()>`

## Code matrix

| CODE | ALLOWED? | RETRYABLE? | MODE | LAYER | USER MESSAGE (FA summary) | WORKER STATUS | LOGGED? |
|------|----------|------------|------|-------|---------------------------|---------------|---------|
| READY | yes | no | both | — | مجاز | n/a | no (success path) |
| ACCOUNT_MISSING | no | no | both | A | اکانت پیدا نشد | failed_permanent | yes |
| WRONG_PLATFORM | no | no | both | A | اکانت روبیکا نیست | failed_permanent | yes |
| ACCOUNT_BANNED | no | no | both | B | بن شده | failed_permanent | yes |
| ACCOUNT_REQUIRES_LOGIN | no | no | both | B | نیاز به ورود | failed_permanent | yes |
| ACCOUNT_DISABLED | no | no | both | B | استراحت/غیرفعال | failed_permanent | yes |
| CONFIG_INVALID | no | no | both | C | پیکربندی نامعتبر | failed_permanent | yes |
| USER_ACCOUNT_DISABLED | no | no | user_account | C | user_account غیرفعال | failed_permanent | yes |
| SESSION_MISSING | no | no | both | D | سشن نیست | failed_permanent | yes |
| SESSION_INVALID | no | no | both | D | سشن نامعتبر | failed_permanent | yes |
| SESSION_DECRYPT_FAILED | no | no | both | D | رمزگشایی شکست | failed_permanent | yes |
| CAMPAIGN_ACCOUNT_NOT_ALLOWED | no | no | both | E | اکانت کمپین مجاز نیست | failed_permanent | yes |
| ACCOUNT_NOT_IN_ALLOWED_POOL | no | yes | user_account | E | در استخر فاز نیست | failed_retryable | yes |
| OUTSIDE_SEND_WINDOW | no | yes | user_account | E/F | خارج از بازه | failed_retryable | yes |
| COOLDOWN_ACTIVE | no | yes | user_account | F | cooldown | failed_retryable | yes |
| HOURLY_CAP_REACHED | no | yes | user_account | F | سقف ساعتی | failed_retryable | yes |
| REDIS_UNAVAILABLE | no | yes | user_account | G | Redis قطع | failed_retryable | yes |
| UNKNOWN_PREFLIGHT_ERROR | no | yes | both | — | خطای ناشناخته | failed_retryable | yes |

Fail-closed: Redis required for user_account runtime limits → deny on outage.
