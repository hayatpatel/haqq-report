-- Run once in the Supabase SQL editor. Safe to run more than once.
-- Only PROCESSED articles are skipped on later runs, so stories stored during an AI outage get drafted next time.
alter table articles add column if not exists processed boolean default false;
