# Phase 5.6 — GPT Baseline

START_HEAD: `cd316102e79276f4659d4d7c2a2936143dd385a8`  
BRANCH: `feature/rubika-module`  
Working tree at audit start: **clean**

## Classification

| Capability | Classification | Notes |
|---|---|---|
| UI checkbox «استفاده از GPT» | ALREADY_VERIFIED | `create.tsx`; persisted via from-import |
| `Campaign.use_gpt` | ALREADY_VERIFIED | Boolean column; **not wired to prepare** |
| `Campaign.include_products` + Phase 5.5 freeze | ALREADY_VERIFIED | `compose_with_locked_products` exists |
| Template `{{name}}` renderer | ALREADY_VERIFIED | `phase4_prepare.render_campaign_template` |
| `RenderedMessage.queue_payload` JSON | ALREADY_VERIFIED | Safe per-message GPT metadata (no new table) |
| Worker sends persisted `final_text` | ALREADY_VERIFIED | Retry must not call GPT |
| `OPENAI_API_KEY` / model / timeout config | ALREADY_VERIFIED | `core_engine/config.py` + `.env.example` |
| `openai` SDK dependency | ALREADY_VERIFIED | `requirements.txt`; client in `openai_client.py` |
| `gpt_orchestrator.py` | CONFLICTING | Per-recipient Chat Completions; sends **customer name** and **product JSON** to GPT; not a variation pool |
| Campaign GPT variation pool | MISSING | |
| Placeholder guardrail / validator | MISSING | |
| Prepare-time GPT wiring | MISSING | `real_gpt_called` always false |
| GPT preview UI/API | MISSING | |
| Worker campaign-GPT isolation | PARTIAL | Workers do not call campaign GPT; unrelated Rubika voice/status bots already use OpenAI |

## Decision

- New `MessageVariationProvider` package. Do **not** route campaign prepare through `gpt_orchestrator` (that path violates product immutability and PII rules).
- Persist frozen pool + assignment in `RenderedMessage.queue_payload.metadata` (MIGRATIONS = NONE).
- GPT sees **base template only**; products appended by Phase 5.5 after substitution.
- LIVE_OPENAI_BINDING = CONFIG_PENDING unless a real key is used (automated tests never call live OpenAI).
