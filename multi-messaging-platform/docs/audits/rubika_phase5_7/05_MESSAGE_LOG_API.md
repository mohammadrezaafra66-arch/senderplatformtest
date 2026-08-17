# Message log API

List: `GET /campaigns/{id}/recipients`  
RBAC: admin, operator, viewer  
Fields added: `final_text_preview`, `has_more` / `has_long_text`, hash, render batch/version, GPT/product summary flags. Excerpt is a Unicode code-point slice (`LIST_EXCERPT_MAX_CHARS=180`).

Detail: `GET /campaigns/{id}/recipients/{recipient_id}`  
Full `final_text`, sender, campaign/contact/rendered/message ids, attempt, GPT public trace, frozen product facts, timestamps, error code.

Sample preview: `POST /campaigns/render-preview` (admin/operator only). `extra=forbid` — no keys, no client `final_text`.

Campaign detail includes `committed_renders` (first 5 persisted `RenderedMessage` rows) and `latest_render_batch_id`.
