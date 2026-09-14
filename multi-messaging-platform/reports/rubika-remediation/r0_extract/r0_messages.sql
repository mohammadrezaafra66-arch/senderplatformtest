--
-- PostgreSQL database dump
--

\restrict Fppe9F0mgUJHfEW6QqEBPLNcGAd0NiZlY5hPX5TrbgAGprtWvwWBEdEhJ2Ykn5F

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
-- Data for Name: messages; Type: TABLE DATA; Schema: public; Owner: mmp_user
--

COPY public.messages (id, campaign_id, account_id, contact_id, rendered_text, media_ref, dedupe_key, product_snapshot_id, created_at, updated_at) FROM stdin;
1	3	12	2	سلام، این یک پیام آزمایشی سامانه افراکالا است.	\N	campaign:3:contact:2	\N	2026-08-25 12:54:45.935586	2026-08-25 13:14:43.853758
\.


--
-- PostgreSQL database dump complete
--

\unrestrict Fppe9F0mgUJHfEW6QqEBPLNcGAd0NiZlY5hPX5TrbgAGprtWvwWBEdEhJ2Ykn5F

