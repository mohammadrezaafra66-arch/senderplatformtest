--
-- PostgreSQL database dump
--

\restrict Lqnlihk5gLt3Gdmx45OUIL1VtVVgLRg6TEHcYffzyOGYtqT2l5lEVBfAU6zsdNr

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
-- Data for Name: rubika_sender_schedules; Type: TABLE DATA; Schema: public; Owner: mmp_user
--

COPY public.rubika_sender_schedules (id, phase, start_hour, end_hour, max_per_hour, is_active, created_at, updated_at) FROM stdin;
195	day	8	22	50	t	2026-08-24 10:24:36.535222	2026-08-25 13:26:44.67353
\.


--
-- PostgreSQL database dump complete
--

\unrestrict Lqnlihk5gLt3Gdmx45OUIL1VtVVgLRg6TEHcYffzyOGYtqT2l5lEVBfAU6zsdNr

