# Preview Flow

`GET /campaigns/gpt-status` — boolean configured / CONFIG_PENDING. No API key.

`POST /campaigns/gpt-preview` — `{template_text, include_products, requested_count?}` extra=forbid (rejects `openai_api_key`).

Same `MessageVariationProvider` + validator as prepare.

UI (create page, GPT checked): «پیشنهاد متن با GPT», وضعیت, [تولید پیش‌نمایش GPT] / [تولید مجدد], up to 3 samples.

With products: composed sample using Phase 5.5 provider + note:

«نمونه پیش‌نمایش؛ انتخاب نهایی محصولات هنگام آماده‌سازی پیام ثبت می‌شود.»

Preview is not a committed render. Campaign create does not require preview; prepare generates its own pool.

Button disabled while in-flight. Backend 2s preview rate guard.
