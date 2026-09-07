-- Bootstrap schema: run once in the Supabase SQL Editor.
-- Server-only access. A future dashboard must use an authenticated backend.
begin;
create table public.stockwatch_rules (
    device_id text not null default 'raspberrypi',
    id text not null,
    market text not null check (market in ('stock', 'crypto')),
    symbol text not null,
    target text not null,
    band_percent double precision not null default 5 check (band_percent > 0 and band_percent < 100),
    note text not null default '',
    enabled boolean not null default true,
    primary key (device_id, id)
);
create table public.stockwatch_runs (
    id uuid primary key,
    device_id text not null,
    started_at timestamptz not null,
    finished_at timestamptz not null,
    status text not null check (status in ('success', 'error')),
    rule_source text not null,
    results jsonb not null check (jsonb_typeof(results) = 'array')
);
create index stockwatch_runs_device_time on public.stockwatch_runs (device_id, started_at desc);
create table public.stockwatch_alerts (
    device_id text not null,
    local_id bigint not null,
    rule_id text not null,
    sent_epoch double precision not null,
    message text not null,
    primary key (device_id, local_id)
);
alter table public.stockwatch_rules enable row level security;
alter table public.stockwatch_runs enable row level security;
alter table public.stockwatch_alerts enable row level security;
revoke all on public.stockwatch_rules, public.stockwatch_runs, public.stockwatch_alerts from public, anon, authenticated;
grant select, insert, update, delete on public.stockwatch_rules, public.stockwatch_runs, public.stockwatch_alerts to service_role;
commit;
