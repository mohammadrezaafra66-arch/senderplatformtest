--
-- PostgreSQL database dump
--

\restrict baATil6elg0bGG31qGVZoyKa0OHGkosI1gpSkgdw1lLcDPAqSa6M5C52rFw9soX

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
-- Data for Name: campaigns; Type: TABLE DATA; Schema: public; Owner: mmp_user
--

COPY public.campaigns (id, title, platform, status, template_text, use_gpt, include_products, schedule_start_at, live_rate_revision, created_at, updated_at, name, channel, intent, message_goal, max_contacts, daily_limit) FROM stdin;
1	rubika test1	RUBIKA	draft	سلام {{first_name}}،	t	t	\N	\N	2026-08-25 11:19:15.287163	2026-08-25 11:19:15.287171	rubika test1	rubika	\N	\N	\N	\N
2	rubikaa test 2	RUBIKA	draft	سلام {{first_name}}، پیام تست کمپین.	t	t	\N	\N	2026-08-25 11:29:07.388797	2026-08-25 11:29:07.3888	rubikaa test 2	rubika	\N	\N	\N	\N
3	ACCOUNT12-PILOT-ONE-MESSAGE	RUBIKA	paused	سلام، این یک پیام آزمایشی سامانه افراکالا است.	f	f	\N	\N	2026-08-25 12:54:44.500628	2026-08-26 10:39:56.391302	ACCOUNT12-PILOT-ONE-MESSAGE	rubika	\N	\N	\N	\N
4	1	RUBIKA	draft	سلام {{first_name}}، پیام تست کمپین.	t	t	\N	\N	2026-08-26 11:03:54.927703	2026-08-26 11:03:54.927707	1	rubika	\N	\N	\N	\N
\.


--
-- PostgreSQL database dump complete
--

\unrestrict baATil6elg0bGG31qGVZoyKa0OHGkosI1gpSkgdw1lLcDPAqSa6M5C52rFw9soX

