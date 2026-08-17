# Preview semantics

Three distinct previews:

| Kind | Endpoint / source | Label | Committed? |
| --- | --- | --- | --- |
| A GPT suggestion | `POST /campaigns/gpt-preview` | پیشنهاد متن GPT (غیرنهایی) | No |
| B Campaign sample | `POST /campaigns/render-preview` | پیش‌نمایش نمونه | No |
| C Committed final | `RenderedMessage.final_text` | پیام نهایی ثبت‌شده | Yes |

B uses `compose_final_render` with synthetic preview variables (`first_name=نمونه` by default). Count default 3, max 5. One GPT pool per preview request. Product selections in B are sample snapshots and must not be implied equal to a later committed selection.

C is loaded from DB after prepare. Campaign detail section: «نمونه پیام‌های نهایی آماده‌شده». If none: «هنوز پیام نهایی ثبت نشده است.»

Create-page four modes (GPT × products) call B. GPT suggestion (A) remains available when `use_gpt` is on and is labeled non-final.

Required unknown placeholders are returned as `preview_variables_required` and are not silently invented.
