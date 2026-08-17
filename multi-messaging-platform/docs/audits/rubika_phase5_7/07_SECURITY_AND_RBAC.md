# Security and RBAC

- Message list/detail/export: admin, operator, viewer (existing reports policy).
- Render/GPT preview and campaign mutate: admin, operator.
- Full message text is business-sensitive and is not returned on unauthorized roles.
- Final text is always rendered as a React text/`<pre>` node (XSS-safe). Scripts in message bodies stay characters.
- Preview/log JSON must not contain API keys, Authorization headers, or raw OpenAI payloads.
- Server logs record ids/codes, not full customer message text + phone.
- Frontend cannot POST committed `final_text`.
