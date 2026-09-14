--
-- PostgreSQL database dump
--

\restrict dYuqLp4cfHX3c25OhCVPpO1TvrYVZkCZMR3MkcTdGlrJHnAOd7t4ETxBZquiXeM

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
-- Data for Name: contacts; Type: TABLE DATA; Schema: public; Owner: mmp_user
--

COPY public.contacts (id, first_name, last_name, phone_e164, telegram_hint, locale, consent_status, blacklisted, extra_variables, created_at, updated_at, source_import_id, source_import_row_id, campaign_id, full_name, phone, channel_handle, tags, raw_payload) FROM stdin;
1	مهرداد	سعادت	+989125137623	\N	fa-IR	allowed	f	{}	2026-08-25 11:17:55.153422	2026-08-25 11:17:55.153423	1	1	\N	\N	+989125137623	\N	\N	\N
2	Pilot	\N	989921589743	\N	\N	allowed	f	{"rubika_guid": "u0JqV6v01cbf2c7fde51215a24c1dc21"}	2026-08-25 12:54:44.497258	2026-08-26 10:32:33.592015	\N	\N	\N	\N	09921589743	\N	\N	\N
\.


--
-- PostgreSQL database dump complete
--

\unrestrict dYuqLp4cfHX3c25OhCVPpO1TvrYVZkCZMR3MkcTdGlrJHnAOd7t4ETxBZquiXeM

