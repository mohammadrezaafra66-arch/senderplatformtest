--
-- PostgreSQL database dump
--

\restrict 4SFcVwl697Nfy6urg88mrX0P1cTcjwsemI2IX9JEiZQ1dBHb75N4r8RPiCWnOBK

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
-- Data for Name: campaign_recipients; Type: TABLE DATA; Schema: public; Owner: mmp_user
--

COPY public.campaign_recipients (id, campaign_id, contact_id, render_status, send_status, final_message_id, created_at, updated_at, failure_reason) FROM stdin;
1	1	1	PENDING	PENDING	\N	2026-08-25 11:19:15.295263	2026-08-25 11:19:15.295266	\N
2	2	1	PENDING	PENDING	\N	2026-08-25 11:29:07.392156	2026-08-25 11:29:07.392158	\N
3	3	2	PENDING	DELIVERED	1	2026-08-25 12:54:44.505573	2026-08-26 10:32:33.858184	\N
4	4	1	PENDING	PENDING	\N	2026-08-26 11:03:54.933692	2026-08-26 11:03:54.933694	\N
\.


--
-- PostgreSQL database dump complete
--

\unrestrict 4SFcVwl697Nfy6urg88mrX0P1cTcjwsemI2IX9JEiZQ1dBHb75N4r8RPiCWnOBK

