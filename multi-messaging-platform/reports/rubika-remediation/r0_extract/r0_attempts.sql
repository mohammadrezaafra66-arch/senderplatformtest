--
-- PostgreSQL database dump
--

\restrict uLHEouZ2HaQ7RdRKn50FKswjh03yv0thjqw3GDMgNLjmyziTyhp3YTsGPUMH7pi

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
-- Data for Name: message_attempts; Type: TABLE DATA; Schema: public; Owner: mmp_user
--

COPY public.message_attempts (id, message_id, attempt_no, status, started_at, accepted_at, platform_message_id, error_code, error_message, created_at) FROM stdin;
1	1	1	FAILED_RETRYABLE	2026-08-25 13:18:39.289069	\N	\N	rubika_account_not_in_allowed_pool	اکانت در استخر فاز فعال روبیکا نیست.	2026-08-25 13:18:39.295525
2	1	2	SUCCESS	2026-08-26 10:32:33.857413	2026-08-26 10:32:33.857415	rubika-user-1885721556412725	\N	\N	2026-08-26 10:32:33.859705
\.


--
-- PostgreSQL database dump complete
--

\unrestrict uLHEouZ2HaQ7RdRKn50FKswjh03yv0thjqw3GDMgNLjmyziTyhp3YTsGPUMH7pi

