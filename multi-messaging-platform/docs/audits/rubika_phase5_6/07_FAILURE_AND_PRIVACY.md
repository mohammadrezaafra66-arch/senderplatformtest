# Failure and Privacy

Persian prepare failure: «تولید متن‌های متنوع با GPT انجام نشد؛ آماده‌سازی کمپین متوقف شد.»

Codes: `GPT_NOT_CONFIGURED`, `GPT_UNAVAILABLE`, `GPT_TIMEOUT`, `GPT_RATE_LIMITED`, `GPT_INVALID_RESPONSE`, `GPT_VARIATION_INVALID`, `GPT_INSUFFICIENT_VARIATIONS`.

`use_gpt=true` + failure → no staged/rendered/message rows; `_auto_prepare` re-raises. No silent base-template fallback.

Privacy: GPT input is the base template (plus application wrapper). Tests assert `{{first_name}}` is present and recipient name/phone/contact_id are absent.

Logs: `gpt_variation_generation_*`, `gpt_variation_validation_failed`, `gpt_preview_generated`, `gpt_variation_pool_frozen`, `gpt_variation_assigned`. No API key, no Authorization header, no recipient PII, no full campaign body at INFO.
