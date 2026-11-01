-- app: the money path. Owner: Abhi.
--
-- Tables from the design doc's "Core tables" and "Plumbing tables", one owner each.
-- Every table carries dataset_id, so a demo reset or an attack-lab scenario starts a fresh
-- dataset instead of deleting rows. Bank account numbers are never stored in full (ADR-8).
--
-- Writes to cases, decisions, releases and the receipts table happen only through
-- app.apply_case_event() and app.release_case() (0002_app_functions.sql). Not even the
-- service role may write them directly: that is the "one door" of ADR-1.

-- Types -------------------------------------------------------------------------------

create type app.case_state as enum (
  'open', 'held', 'calling', 'retry_wait', 'deciding',
  'awaiting_release', 'needs_review', 'blocked', 'releasing', 'released'
);
comment on type app.case_state is
  'The 10 case states. released is the only finished state; blocked keeps the hold on and waits for a person or a new change.';

create type app.case_outcome   as enum ('verified', 'not_verified', 'needs_review');
create type app.actor_kind     as enum ('system', 'person');
create type app.detected_via   as enum ('webhook', 'poll', 'email');
create type app.release_action as enum ('approve', 'reject');
create type app.user_role      as enum ('clerk', 'approver', 'auditor');
create type app.command_kind   as enum ('place_hold', 'select_number', 'place_call', 'decide', 'release_hold', 'notify');
create type app.command_status as enum ('pending', 'running', 'done', 'failed');
create type app.timer_kind     as enum ('calling_window', 'retry_call', 'callback', 'deadline');
create type app.timer_status   as enum ('scheduled', 'fired', 'cancelled');
create type app.dataset_kind   as enum ('dev', 'demo', 'lab');
create type app.hold_status    as enum ('placing', 'confirmed', 'failed', 'releasing', 'released');
create type app.alert_severity as enum ('info', 'warning', 'critical');

-- Datasets and people ------------------------------------------------------------------

create table app.datasets (
  id         uuid primary key default gen_random_uuid(),
  kind       app.dataset_kind not null,
  label      text not null,
  chain_id   uuid not null references audit.chains (id),   -- one evidence chain per dataset
  created_at timestamptz not null default now(),
  retired_at timestamptz
);

-- Dialback users are Supabase auth users. Profiles add the Ramp identity so the audit log's
-- actor can be matched to the person who entered a change (R5, ADR-10).
create table app.user_profiles (
  user_id      uuid primary key,
  display_name text not null,
  ramp_user_id text unique,
  created_at   timestamptz not null default now()
);

create table app.user_roles (
  user_id    uuid not null,
  role       app.user_role not null,
  granted_at timestamptz not null default now(),
  primary key (user_id, role)
);

do $$
begin
  if to_regclass('auth.users') is not null then
    alter table app.user_profiles
      add constraint user_profiles_user_fk foreign key (user_id) references auth.users (id) on delete cascade;
    alter table app.user_roles
      add constraint user_roles_user_fk foreign key (user_id) references auth.users (id) on delete cascade;
  end if;
end
$$;

-- Vendors and bank accounts (mirrored from Ramp) ---------------------------------------

create table app.vendors (
  id              uuid primary key default gen_random_uuid(),
  dataset_id      uuid not null references app.datasets (id),
  ramp_vendor_id  text not null,
  legal_name      text not null,
  trade_names     text[] not null default '{}',
  tin_hash        text,                       -- keyed hash of the tax id, never the tax id
  ramp_status     text,
  ramp_updated_at timestamptz,
  synced_at       timestamptz not null default now(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (dataset_id, ramp_vendor_id)
);

create table app.bank_accounts (
  id                      uuid primary key default gen_random_uuid(),
  dataset_id              uuid not null references app.datasets (id),
  vendor_id               uuid not null references app.vendors (id),
  ramp_account_id         text,
  routing_number          text not null,
  account_last4           text not null check (account_last4 ~ '^[0-9]{4}$'),
  account_fingerprint     text not null check (account_fingerprint ~ '^[0-9a-f]{64}$'),  -- HMAC-SHA256(key, routing || account)
  fingerprint_key_version integer not null default 1,
  bank_name               text,
  account_type            text,
  first_seen_at           timestamptz not null default now(),
  last_seen_at            timestamptz not null default now(),
  is_current              boolean not null default true,
  created_at              timestamptz not null default now(),
  unique (dataset_id, vendor_id, account_fingerprint, fingerprint_key_version)
);
comment on column app.bank_accounts.account_fingerprint is
  'HMAC-SHA256 over routing || account with a server-side key (ADR-8). Enough to compare, useless if leaked.';

create table app.bank_change_events (
  id                      uuid primary key default gen_random_uuid(),
  dataset_id              uuid not null references app.datasets (id),
  vendor_id               uuid not null references app.vendors (id),
  old_account_id          uuid references app.bank_accounts (id),
  new_account_id          uuid not null references app.bank_accounts (id),
  detected_via            app.detected_via not null,
  source_event_id         text not null,     -- Ramp event id, poll diff key or email message id
  changed_by              uuid,              -- Dialback user who made the change; null = unknown (ADR-10)
  changed_by_ramp_user_id text,              -- actor from Ramp's audit log, when available
  change_thread_id        text,              -- email thread id: the Trust Ledger's change context (R2)
  ramp_changed_at         timestamptz,
  detected_at             timestamptz not null default now(),
  unique (dataset_id, source_event_id)       -- raw and deduplicated
);

-- Cases ----------------------------------------------------------------------------------

create table app.cases (
  id                  uuid primary key default gen_random_uuid(),
  dataset_id          uuid not null references app.datasets (id),
  vendor_id           uuid not null references app.vendors (id),
  change_event_id     uuid not null references app.bank_change_events (id),  -- the latest change
  state               app.case_state not null default 'open',
  outcome             app.case_outcome,
  version             integer not null default 1,     -- change generation; +1 when another change arrives
  entered_by          uuid,                           -- first known changer (R5); null = unknown
  changer_ids         uuid[] not null default '{}',   -- every known changer across generations
  has_unknown_changer boolean not null default false, -- true => release needs two approvers (ADR-10)
  reviewed_by         uuid,                           -- person who cleared needs_review, if any
  policy_version      text not null,
  call_attempts       integer not null default 0,
  max_call_attempts   integer not null default 3,
  deadline_at         timestamptz,
  opened_at           timestamptz not null default now(),
  closed_at           timestamptz,
  updated_at          timestamptz not null default now()
);

-- One unfinished case per vendor. A second change joins the open case (version + 1).
create unique index cases_one_unfinished_per_vendor
  on app.cases (dataset_id, vendor_id) where state <> 'released';
create index cases_state_idx on app.cases (dataset_id, state);

create table app.holds (
  id                   uuid primary key default gen_random_uuid(),
  dataset_id           uuid not null references app.datasets (id),
  case_id              uuid not null references app.cases (id),
  vendor_id            uuid not null references app.vendors (id),
  ramp_ref             text,
  status               app.hold_status not null default 'placing',
  original_schedule    jsonb not null default '[]',   -- [{"bill_id": "...", "payment_date": "YYYY-MM-DD"}]
  placed_at            timestamptz,
  confirmed_at         timestamptz,
  released_at          timestamptz,
  release_confirmed_at timestamptz,
  last_error           text,
  created_at           timestamptz not null default now()
);
create unique index holds_one_active_per_case on app.holds (case_id) where released_at is null;

create table app.releases (
  id           uuid primary key default gen_random_uuid(),
  dataset_id   uuid not null references app.datasets (id),
  case_id      uuid not null references app.cases (id),
  case_version integer not null,
  actor_id     uuid not null,
  action       app.release_action not null,
  reason       text not null check (length(btrim(reason)) > 0),
  created_at   timestamptz not null default now()
);
create index releases_case_idx on app.releases (case_id, case_version);

create table app.decisions (
  id               uuid primary key default gen_random_uuid(),
  dataset_id       uuid not null references app.datasets (id),
  case_id          uuid not null references app.cases (id),
  case_version     integer not null,
  outcome          app.case_outcome not null,
  rules_fired      text[] not null default '{}',
  inputs_hash      text not null check (inputs_hash ~ '^[0-9a-f]{64}$'),
  engine_version   text not null,
  strict_mode      boolean not null default false,
  last_four_result text check (last_four_result in
                     ('match', 'matches_old_account', 'near_miss', 'mismatch', 'not_provided', 'ambiguous', 'inconsistent')),
  source           text not null default 'call' check (source in ('call', 'manual')),
  decided_at       timestamptz not null default now(),
  voided_at        timestamptz                        -- set when a later change voids this decision
);
create index decisions_case_idx on app.decisions (case_id, case_version);

-- The state machine as data. CI redraws docs/state-machine.md from these rows.
create table app.case_transitions (
  from_state  app.case_state,                  -- null = a new case
  event_type  text not null,
  actor_kind  app.actor_kind not null,
  guard_key   text not null default 'always',  -- evaluated by app.eval_guard()
  to_state    app.case_state not null,
  description text not null
);
alter table app.case_transitions
  add constraint case_transitions_pk unique nulls not distinct (from_state, event_type, guard_key);

-- Plumbing -------------------------------------------------------------------------------

create table app.case_event_receipts (
  idempotency_key text primary key,
  case_id         uuid not null references app.cases (id),
  event_type      text not null,
  result          jsonb not null,
  applied_at      timestamptz not null default now()
);

create table app.outbox (
  id              bigint generated always as identity primary key,
  dataset_id      uuid not null references app.datasets (id),
  case_id         uuid references app.cases (id),
  command         app.command_kind not null,
  idempotency_key text not null unique,     -- {case_id}:hold:v{n}, {case_id}:call:{n}, {case_id}:release:v{n}
  payload         jsonb not null default '{}',
  status          app.command_status not null default 'pending',
  run_after       timestamptz not null default now(),
  attempts        integer not null default 0,
  max_attempts    integer not null default 5,
  locked_at       timestamptz,
  locked_by       text,
  last_error      text,
  created_at      timestamptz not null default now(),
  done_at         timestamptz
);
create index outbox_due_idx on app.outbox (run_after) where status = 'pending';
create index outbox_case_idx on app.outbox (case_id);

create table app.timers (
  id           bigint generated always as identity primary key,
  dataset_id   uuid not null references app.datasets (id),
  case_id      uuid not null references app.cases (id),
  kind         app.timer_kind not null,
  fire_at      timestamptz not null,
  payload      jsonb not null default '{}',
  status       app.timer_status not null default 'scheduled',
  fired_at     timestamptz,
  cancelled_at timestamptz,
  created_at   timestamptz not null default now()
);
create index timers_due_idx on app.timers (fire_at) where status = 'scheduled';
create index timers_case_idx on app.timers (case_id);

create table app.inbound_webhooks (
  id              bigint generated always as identity primary key,
  dataset_id      uuid not null references app.datasets (id),
  source          text not null default 'ramp',
  event_id        text not null,            -- Ramp's event id; for unsigned deliveries, 'unsigned:' || sha256(body)
  event_type      text,
  signature_valid boolean not null,
  headers         jsonb not null default '{}',
  raw_body        text not null,
  received_at     timestamptz not null default now(),
  processed_at    timestamptz,
  error           text,
  unique (source, event_id)                 -- duplicates are acknowledged with 2xx and dropped here
);

create table app.alerts (
  id          bigint generated always as identity primary key,
  dataset_id  uuid not null references app.datasets (id),
  case_id     uuid references app.cases (id),
  kind        text not null,                -- hold_not_confirmed, vendor_not_held, chain_broken, command_stuck,
                                            -- bad_webhook_signature, no_provable_number, deadline_reached, not_verified
  severity    app.alert_severity not null,
  message     text not null,
  raised_at   timestamptz not null default now(),
  resolved_at timestamptz
);
create index alerts_open_idx on app.alerts (dataset_id, severity) where resolved_at is null;

-- Housekeeping triggers --------------------------------------------------------------------

create function app.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end
$$;
create trigger vendors_touch before update on app.vendors for each row execute function app.touch_updated_at();
create trigger cases_touch   before update on app.cases   for each row execute function app.touch_updated_at();

-- R5 at the table: a release or rejection by the changer is refused, and the reviewer who
-- cleared a case cannot also approve it.
create function app.releases_four_eyes() returns trigger
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  c app.cases%rowtype;
begin
  select * into c from app.cases where id = new.case_id;
  if new.actor_id = any (c.changer_ids) then
    raise exception 'R5: the person who entered the change cannot release or reject it'
      using errcode = 'check_violation';
  end if;
  if new.action = 'approve' and c.reviewed_by is not null and new.actor_id = c.reviewed_by then
    raise exception 'R5: the reviewer who cleared the case cannot also approve its release'
      using errcode = 'check_violation';
  end if;
  if new.case_version <> c.version then
    raise exception 'release recorded for version % but the case is at version %', new.case_version, c.version
      using errcode = 'check_violation';
  end if;
  return new;
end
$$;
create trigger releases_four_eyes before insert on app.releases
  for each row execute function app.releases_four_eyes();

-- Realtime for the dashboard (only where the Supabase publication exists).
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table app.cases, app.alerts;
  end if;
end
$$;

-- Access ---------------------------------------------------------------------------------
-- Everyone signed in can read (the dashboard). The worker (service role) writes mirrors and
-- plumbing directly. cases, decisions, releases and receipts are written only by the SQL
-- functions, which run as their owner.

alter table app.datasets            enable row level security;
alter table app.user_profiles       enable row level security;
alter table app.user_roles          enable row level security;
alter table app.vendors             enable row level security;
alter table app.bank_accounts       enable row level security;
alter table app.bank_change_events  enable row level security;
alter table app.cases               enable row level security;
alter table app.holds               enable row level security;
alter table app.releases            enable row level security;
alter table app.decisions           enable row level security;
alter table app.case_transitions    enable row level security;
alter table app.case_event_receipts enable row level security;
alter table app.outbox              enable row level security;
alter table app.timers              enable row level security;
alter table app.inbound_webhooks    enable row level security;
alter table app.alerts              enable row level security;

create policy datasets_read            on app.datasets            for select to authenticated using (true);
create policy user_profiles_read       on app.user_profiles       for select to authenticated using (true);
create policy user_roles_read          on app.user_roles          for select to authenticated using (true);
create policy vendors_read             on app.vendors             for select to authenticated using (true);
create policy bank_accounts_read       on app.bank_accounts       for select to authenticated using (true);
create policy bank_change_events_read  on app.bank_change_events  for select to authenticated using (true);
create policy cases_read               on app.cases               for select to authenticated using (true);
create policy holds_read               on app.holds               for select to authenticated using (true);
create policy releases_read            on app.releases            for select to authenticated using (true);
create policy decisions_read           on app.decisions           for select to authenticated using (true);
create policy case_transitions_read    on app.case_transitions    for select to authenticated using (true);
create policy case_event_receipts_read on app.case_event_receipts for select to authenticated using (true);
create policy outbox_read              on app.outbox              for select to authenticated using (true);
create policy timers_read              on app.timers              for select to authenticated using (true);
create policy inbound_webhooks_read    on app.inbound_webhooks    for select to authenticated using (true);
create policy alerts_read              on app.alerts              for select to authenticated using (true);

revoke all on all tables in schema app from public, anon, authenticated, service_role;
grant select on all tables in schema app to authenticated, service_role;
grant insert, update on app.datasets, app.user_profiles, app.user_roles, app.vendors, app.bank_accounts,
                       app.bank_change_events, app.holds, app.outbox, app.timers, app.inbound_webhooks, app.alerts
  to service_role;
grant usage, select on all sequences in schema app to service_role;
