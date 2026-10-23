-- Run once in the Supabase SQL editor if db/schema.sql was applied before run logging existed.
-- Safe to run more than once.
alter table articles add column if not exists source_name text;

create table if not exists run_logs (
  id          bigint generated always as identity primary key,
  name        text not null,
  started_at  timestamptz not null,
  finished_at timestamptz,
  status      text not null,
  summary     jsonb default '{}'::jsonb,
  log_text    text,
  created_at  timestamptz default now()
);
alter table run_logs enable row level security;

-- service_role only: the tables stay private (see db/grant_service_role.sql)
grant select, insert, update, delete on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;
