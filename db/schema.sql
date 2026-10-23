-- Haqq Report: Supabase schema (plan section 14).
-- Run once in the Supabase SQL editor. Row Level Security is enabled on every table with NO policies,
-- so the public anon key can read nothing. Only the service key (stored in GitHub secrets and the
-- Cloudflare Worker secrets) can access these tables.

create table if not exists sources (
  id            bigint generated always as identity primary key,
  name          text unique not null,
  tier          text not null check (tier in ('A','B','C','D')),
  language      text default 'en',
  country       text,
  feed_url      text,
  funding       text,
  bias_note     text,
  state_funded  boolean default false,
  priority      boolean default false,
  last_checked  timestamptz
);

create table if not exists creators (
  id                bigint generated always as identity primary key,
  handle            text unique not null,
  name              text,
  score             numeric(3,1) check (score between 0 and 10),
  corrections_count int default 0,
  permission        boolean default false,   -- permission to reuse photo/video
  years_active      int,
  affiliation       text,
  language          text,
  last_scored       timestamptz
);

create table if not exists articles (
  id           bigint generated always as identity primary key,
  url          text unique not null,
  title        text not null,
  summary      text,
  source_id    bigint references sources(id),
  tier         text,
  language     text default 'en',
  translated   boolean default false,
  published_at timestamptz,
  score        numeric(5,3),
  fetched_at   timestamptz default now()
);

create table if not exists claims (
  id           bigint generated always as identity primary key,
  article_id   bigint references articles(id) on delete cascade,
  text         text not null,
  attribution  text not null,
  status       text default 'unverified' check (status in ('unverified','verified','on_ground','rejected')),
  verified_by  text
);

create table if not exists factchecks (
  id            bigint generated always as identity primary key,
  claim         text not null,
  verdict       text not null,
  evidence_urls text[] default '{}',
  checker       text,
  created_at    timestamptz default now()
);

create table if not exists drafts (
  id         bigint generated always as identity primary key,
  article_id bigint references articles(id),
  lane       text not null check (lane in ('green','amber','red')),
  status     text not null default 'pending' check (status in ('pending','approved','rejected','posted','skipped')),
  headline   text not null,
  body       text not null,
  sources    text[] default '{}',
  label      text,
  flags      text[] default '{}',
  created_at timestamptz default now(),
  decided_at timestamptz
);

create table if not exists posts (
  id               bigint generated always as identity primary key,
  draft_id         bigint references drafts(id),
  platform         text not null,
  platform_post_id text,
  posted_at        timestamptz default now()
);

create table if not exists corrections (
  id                     bigint generated always as identity primary key,
  post_id                text not null,
  what_changed           text not null,
  kind                   text default 'correction',
  manual_removal_pending boolean default false,
  date                   date default current_date
);

create table if not exists occasions (
  id           bigint generated always as identity primary key,
  name         text not null,
  hijri        text,
  gregorian    date,
  region_note  text
);

-- Token bookkeeping: token age cannot be read from the token itself.
create table if not exists tokens (
  platform       text primary key,          -- 'instagram' | 'threads' | 'facebook'
  issued_at      timestamptz,
  last_refreshed timestamptz
);

-- Runtime switches set from Telegram (/pause /resume /auto /review).
create table if not exists settings (
  key   text primary key,
  value text not null
);
insert into settings (key, value) values ('paused', 'false'), ('mode', 'review')
  on conflict (key) do nothing;

alter table sources     enable row level security;
alter table creators    enable row level security;
alter table articles    enable row level security;
alter table claims      enable row level security;
alter table factchecks  enable row level security;
alter table drafts      enable row level security;
alter table posts       enable row level security;
alter table corrections enable row level security;
alter table occasions   enable row level security;
alter table tokens      enable row level security;
alter table settings    enable row level security;

-- ---------------------------------------------------------------------------------------
-- Supabase no longer opens new public tables to the Data API automatically.
-- The bot uses the secret key (role service_role), so give that role access.
-- anon and authenticated get NOTHING: these tables stay private.
grant usage on schema public to service_role;
grant select, insert, update, delete on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;
-- tables created later by the SQL editor get the same grants
alter default privileges in schema public grant select, insert, update, delete on tables to service_role;
alter default privileges in schema public grant usage, select on sequences to service_role;
