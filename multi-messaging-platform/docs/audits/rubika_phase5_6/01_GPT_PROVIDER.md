# GPT Provider

## Interface
`MessageVariationProvider.generate_variations(template_text, protected_placeholders, requested_count, language, instructions) -> MessageVariationResult`

## Implementation
`OpenAIMessageVariationProvider` — official OpenAI SDK **Responses API** (`client.responses.create`), `store=False`, `max_retries=0`, timeout from settings.

Tests use `FakeMessageVariationProvider` (no network).

## Config (environment only)
`OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, `OPENAI_VARIATION_COUNT` (default 5, clamp 3–8), `OPENAI_MAX_OUTPUT_TOKENS`, `OPENAI_VARIATION_MIN_CHARS`, `OPENAI_VARIATION_MAX_CHARS`, `OPENAI_VARIATION_MAX_ATTEMPTS` (default 2).

Empty/placeholder key → `GPT_NOT_CONFIGURED` when `use_gpt=true`. `use_gpt=false` never calls the provider.

## LIVE_OPENAI_BINDING
**CONFIG_PENDING** in automated tests (no live calls).

## Dependency
Existing `openai` in `requirements.txt` (no new pin). Campaign variation does **not** use `gpt_orchestrator.py`.
