# Remaining gaps

- No frontend unit-test runner (no Jest/Vitest). XSS/line-clamp/More are covered by React text rendering + TypeScript/ESLint/build.
- Preview→commit reuse is intentionally not implemented (no batch token).
- Optional list filters (sender / GPT used / products) were not added; campaign + send status remain.
- `ready` re-prepare still regenerates unsent rows (existing Phase 4 contract) under a new `render_batch_id`.
