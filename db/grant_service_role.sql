-- Run once in the Supabase SQL editor if db/schema.sql was applied before these grants existed.
-- Safe to run more than once.
-- Supabase no longer opens new public tables to the Data API automatically.
-- The bot uses the secret key (role service_role), so give that role access.
-- anon and authenticated get NOTHING: these tables stay private.
grant usage on schema public to service_role;
grant select, insert, update, delete on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;
-- tables created later by the SQL editor get the same grants
alter default privileges in schema public grant select, insert, update, delete on tables to service_role;
alter default privileges in schema public grant usage, select on sequences to service_role;
