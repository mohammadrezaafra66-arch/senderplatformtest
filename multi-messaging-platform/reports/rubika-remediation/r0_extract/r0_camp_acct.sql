--
-- PostgreSQL database dump
--

\restrict Nxca9UuWhhO5gHMUisCO085w3cfH5BXjg3ACd7d6z9qerxsHkRGUEUIasvjEbOB

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
-- Data for Name: campaign_accounts; Type: TABLE DATA; Schema: public; Owner: mmp_user
--

COPY public.campaign_accounts (id, campaign_id, account_id, priority, weight, enabled, created_at) FROM stdin;
1	1	92	1	1	t	2026-08-25 11:19:15.280762
2	2	79	1	1	t	2026-08-25 11:29:07.386369
3	3	12	1	1	t	2026-08-25 12:54:44.490452
4	4	12	1	1	t	2026-08-26 11:03:54.923693
6	4	79	2	1	t	2026-08-26 11:06:08.500981
\.


--
-- PostgreSQL database dump complete
--

\unrestrict Nxca9UuWhhO5gHMUisCO085w3cfH5BXjg3ACd7d6z9qerxsHkRGUEUIasvjEbOB

