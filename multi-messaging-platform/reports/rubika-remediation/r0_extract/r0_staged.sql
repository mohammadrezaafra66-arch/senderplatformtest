--
-- PostgreSQL database dump
--

\restrict HAp3XvdSbwFxCygpQr2XWz0MXoUEfHonbQZTuxhLKeYwagtQd1IcYuVF1PzR5pG

-- Dumped from database version 15.18 (Debian 15.18-1.pgdg13+1)
-- Dumped by pg_dump version 15.18 (Debian 15.18-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: staged_queue_items; Type: TABLE DATA; Schema: public; Owner: mmp_user
--

COPY public.staged_queue_items (id, campaign_id, contact_id, rendered_message_id, channel, status, final_text, queue_payload, skip_reason, created_at) FROM stdin;
1	3	2	1	rubika	queued	سلام، این یک پیام آزمایشی سامانه افراکالا است.	{"phone": "+989921589743", "attempt": 2, "channel": "rubika", "dry_run": true, "metadata": {"use_gpt": false, "render_version": "campaign-render-v1", "render_batch_id": "9d12d94babd54ef9aa8b2c9165a663df", "template_source": "base_template", "include_products": false, "final_text_sha256": "d68e0508e088cdf08ad3a48f104af7d4b7e42f1e2a181403d91ea4db410683ed"}, "account_id": 12, "contact_id": 2, "final_text": "سلام، این یک پیام آزمایشی سامانه افراکالا است.", "message_id": 1, "campaign_id": 3, "safety_note": "DB staging only. Not pushed to Redis.", "channel_handle": null, "ready_for_queue": true, "rendered_message_id": 1, "real_queue_push_enabled": true}	\N	2026-08-25 12:54:45.944625
\.


--
-- PostgreSQL database dump complete
--

\unrestrict HAp3XvdSbwFxCygpQr2XWz0MXoUEfHonbQZTuxhLKeYwagtQd1IcYuVF1PzR5pG

