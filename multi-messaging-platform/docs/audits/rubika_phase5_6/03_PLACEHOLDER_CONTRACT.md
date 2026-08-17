# Placeholder Contract

Syntax matches the campaign renderer: `{{identifier}}` (`TEMPLATE_PLACEHOLDER`).

Extracted from the **base template** before GPT. Recipient substitution happens **after** variation.

Guardrails:
- missing placeholder → reject
- unknown placeholder → reject
- `{first_name}` instead of `{{first_name}}` → malformed → reject

Prefer rejection / bounded regeneration over silent repair.
