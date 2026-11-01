-- mock_ramp: a Ramp-shaped stand-in for the Developer API. Owner: Abhi.
--
-- The FastAPI mock (apps/ramp-mock) serves Ramp's paths over these tables and keeps the
-- rate limiter in memory. Behavior that must match Ramp lives here as SQL functions so the
-- walking skeleton, the attack lab and the tests all see the same rules:
--
--   mock_ramp.change_bank_account()  a new account, an audit event, a vendors.updated webhook
--                                    (unless silent_changes is on)
--   mock_ramp.place_hold()           clears every unpaid bill's payment_date (as Ramp does)
--   mock_ramp.release_hold()         lifts the hold and does NOT reschedule the bills
--   mock_ramp.consume_switch()       reads and clears a one-shot chaos switch (fail_next_hold,
--                                    fail_next_release); the API calls it in its own transaction
--                                    and answers 500 when it returns true
--
-- One mock business per Dialback dataset. Field names follow Ramp's API where we know
-- them; columns marked "confirm" are to be checked against Ramp's OpenAPI spec, and
-- anything unexpected goes in the extra jsonb column until the spec settles it.

create table mock_ramp.businesses (
  id         uuid primary key default gen_random_uuid(),
  dataset_id uuid not null unique,          -- app.datasets.id; no FK so the mock can run alone
  name       text not null,
  created_at timestamptz not null default now()
);

create table mock_ramp.users (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid not null references mock_ramp.businesses (id),
  email       text not null,
  first_name  text not null,
  last_name   text not null,
  role        text not null default 'BUSINESS_USER',   -- confirm: BUSINESS_OWNER | BUSINESS_ADMIN | BUSINESS_USER | ...
  status      text not null default 'USER_ACTIVE',     -- confirm
  created_at  timestamptz not null default now(),
  unique (business_id, email)
);

create table mock_ramp.vendors (
  id              uuid primary key default gen_random_uuid(),
  business_id     uuid not null references mock_ramp.businesses (id),
  name            text not null,
  status          text not null default 'ACTIVE',       -- confirm
  is_active       boolean not null default true,
  type            text,                                 -- confirm: BUSINESS | INDIVIDUAL
  country         text,
  state           text,
  tax_id          text,                                 -- mock only; the API should never return it in full
  remote_id       text,
  payment_method  text default 'ACH',                   -- confirm
  vendor_owner_id uuid references mock_ramp.users (id),
  extra           jsonb not null default '{}',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index mock_vendors_updated_idx on mock_ramp.vendors (business_id, updated_at);

create table mock_ramp.vendor_accounts (
  id             uuid primary key default gen_random_uuid(),
  business_id    uuid not null references mock_ramp.businesses (id),
  vendor_id      uuid not null references mock_ramp.vendors (id),
  routing_number text not null,
  account_number text not null,                         -- full number lives only in the mock; GET returns the last four
  account_type   text not null default 'CHECKING',      -- confirm
  bank_name      text,
  is_active      boolean not null default true,
  created_by     uuid references mock_ramp.users (id),  -- null = vendor-supplied or ERP sync (unknown changer)
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index mock_vendor_accounts_vendor_idx on mock_ramp.vendor_accounts (vendor_id, is_active);

create table mock_ramp.vendor_contacts (
  id           uuid primary key default gen_random_uuid(),
  business_id  uuid not null references mock_ramp.businesses (id),
  vendor_id    uuid not null references mock_ramp.vendors (id),
  first_name   text,
  last_name    text,
  email        text,
  phone_number text,                                    -- E.164
  is_primary   boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table mock_ramp.bills (
  id             uuid primary key default gen_random_uuid(),
  business_id    uuid not null references mock_ramp.businesses (id),
  vendor_id      uuid not null references mock_ramp.vendors (id),
  invoice_number text,
  amount_cents   bigint not null check (amount_cents >= 0),
  currency_code  text not null default 'USD',
  issued_at      date,
  due_at         date,
  payment_date   date,                                  -- cleared by a vendor hold; not restored by a release
  status         text not null default 'OPEN',          -- confirm: OPEN | APPROVED | PAID | ...
  is_on_hold     boolean not null default false,
  paid_at        timestamptz,
  extra          jsonb not null default '{}',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index mock_bills_vendor_idx on mock_ramp.bills (vendor_id, status);

create table mock_ramp.vendor_holds (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid not null references mock_ramp.businesses (id),
  vendor_id   uuid not null references mock_ramp.vendors (id),
  reason      text,
  placed_by   text,
  placed_at   timestamptz not null default now(),
  released_at timestamptz
);
create unique index mock_vendor_holds_one_active on mock_ramp.vendor_holds (vendor_id) where released_at is null;

create table mock_ramp.audit_events (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid not null references mock_ramp.businesses (id),
  event_type  text not null,                            -- confirm Ramp's name for a vendor bank-account change
  actor_id    uuid references mock_ramp.users (id),     -- null = vendor portal or ERP sync
  target_type text not null,
  target_id   uuid,
  payload     jsonb not null default '{}',
  occurred_at timestamptz not null default now()
);
create index mock_audit_events_time_idx on mock_ramp.audit_events (business_id, occurred_at);

create table mock_ramp.webhook_subscriptions (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid not null references mock_ramp.businesses (id),
  url         text not null,
  secret      text not null,                            -- HMAC-SHA256 key for X-Ramp-Signature
  event_types text[] not null default '{vendors.updated}',
  is_active   boolean not null default true,
  created_at  timestamptz not null default now()
);

create table mock_ramp.webhook_deliveries (
  id              bigint generated always as identity primary key,
  subscription_id uuid not null references mock_ramp.webhook_subscriptions (id),
  event_id        uuid not null,                        -- the id Dialback dedups on; duplicates reuse it
  event_type      text not null,
  payload         jsonb not null,
  attempt         integer not null default 1,
  status          text not null default 'scheduled' check (status in ('scheduled', 'delivered', 'failed', 'dropped')),
  scheduled_at    timestamptz not null default now(),
  delivered_at    timestamptz,
  response_status integer,
  last_error      text
);
create index mock_webhook_deliveries_due_idx on mock_ramp.webhook_deliveries (scheduled_at) where status = 'scheduled';

-- Chaos switches, one row per business. The attack lab flips these.
create table mock_ramp.settings (
  business_id         uuid primary key references mock_ramp.businesses (id),
  duplicate_webhooks  boolean not null default false,   -- send every webhook twice
  reorder_webhooks    boolean not null default false,   -- deliver out of order
  silent_changes      boolean not null default false,   -- bank changes send no webhook (the poller must catch them)
  webhook_delay_ms    integer not null default 0,
  rate_limit_per_10s  integer not null default 200,     -- Ramp: 200 requests per 10 seconds per IP
  fail_next_hold      boolean not null default false,   -- the next hold call returns 500
  fail_next_release   boolean not null default false,
  hold_readback_lag_ms integer not null default 0,      -- read-back shows the hold only after this lag
  updated_at          timestamptz not null default now()
);

-- Behavior -----------------------------------------------------------------------------------

create function mock_ramp.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end
$$;
create trigger mock_vendors_touch  before update on mock_ramp.vendors         for each row execute function mock_ramp.touch_updated_at();
create trigger mock_accounts_touch before update on mock_ramp.vendor_accounts for each row execute function mock_ramp.touch_updated_at();
create trigger mock_contacts_touch before update on mock_ramp.vendor_contacts for each row execute function mock_ramp.touch_updated_at();
create trigger mock_bills_touch    before update on mock_ramp.bills           for each row execute function mock_ramp.touch_updated_at();

create function mock_ramp.queue_webhook(p_business_id uuid, p_event_type text, p_payload jsonb) returns integer
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  s        mock_ramp.settings%rowtype;
  v_event  uuid := gen_random_uuid();
  v_n      integer := 0;
  v_sub    record;
  v_delay  interval;
begin
  select * into s from mock_ramp.settings where business_id = p_business_id;
  v_delay := make_interval(secs => coalesce(s.webhook_delay_ms, 0) / 1000.0);
  for v_sub in
    select * from mock_ramp.webhook_subscriptions w
     where w.business_id = p_business_id and w.is_active and p_event_type = any (w.event_types)
  loop
    insert into mock_ramp.webhook_deliveries (subscription_id, event_id, event_type, payload, scheduled_at)
    values (v_sub.id, v_event, p_event_type, p_payload || jsonb_build_object('id', v_event, 'type', p_event_type), now() + v_delay);
    v_n := v_n + 1;
    if coalesce(s.duplicate_webhooks, false) then
      insert into mock_ramp.webhook_deliveries (subscription_id, event_id, event_type, payload, attempt, scheduled_at)
      values (v_sub.id, v_event, p_event_type, p_payload || jsonb_build_object('id', v_event, 'type', p_event_type), 2,
              now() + v_delay + case when coalesce(s.reorder_webhooks, false) then interval '-1 second' else interval '1 second' end);
      v_n := v_n + 1;
    end if;
  end loop;
  return v_n;
end
$$;

-- A vendor's bank details change. Deactivates the old account, records the audit event
-- with the actor (null = vendor-supplied or ERP sync), and queues vendors.updated unless
-- the business is in silent mode.
create function mock_ramp.change_bank_account(
  p_vendor_id      uuid,
  p_routing_number text,
  p_account_number text,
  p_actor_id       uuid default null,
  p_account_type   text default 'CHECKING',
  p_bank_name      text default null
) returns uuid
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  v_vendor  mock_ramp.vendors%rowtype;
  v_old     uuid;
  v_new     uuid;
  s         mock_ramp.settings%rowtype;
begin
  select * into v_vendor from mock_ramp.vendors where id = p_vendor_id for update;
  if not found then
    raise exception 'mock_ramp: unknown vendor %', p_vendor_id;
  end if;
  select id into v_old from mock_ramp.vendor_accounts where vendor_id = p_vendor_id and is_active order by created_at desc limit 1;
  update mock_ramp.vendor_accounts set is_active = false where vendor_id = p_vendor_id and is_active;
  insert into mock_ramp.vendor_accounts (business_id, vendor_id, routing_number, account_number, account_type, bank_name, created_by)
  values (v_vendor.business_id, p_vendor_id, p_routing_number, p_account_number, p_account_type, p_bank_name, p_actor_id)
  returning id into v_new;
  update mock_ramp.vendors set updated_at = now() where id = p_vendor_id;
  insert into mock_ramp.audit_events (business_id, event_type, actor_id, target_type, target_id, payload)
  values (v_vendor.business_id, 'VENDOR_BANK_ACCOUNT_UPDATED', p_actor_id, 'vendor', p_vendor_id,
          jsonb_build_object('old_account_id', v_old, 'new_account_id', v_new, 'account_last4', right(p_account_number, 4)));
  select * into s from mock_ramp.settings where business_id = v_vendor.business_id;
  if not coalesce(s.silent_changes, false) then
    perform mock_ramp.queue_webhook(v_vendor.business_id, 'vendors.updated',
              jsonb_build_object('data', jsonb_build_object('vendor_id', p_vendor_id, 'updated_at', now())));
  end if;
  return v_new;
end
$$;

-- POST /developer/v1/vendors/{id}/hold. Clears every unpaid bill's payment date, as Ramp
-- does, which is why Dialback saves the schedule first.
create function mock_ramp.place_hold(p_vendor_id uuid, p_reason text default null, p_placed_by text default null) returns uuid
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  v_vendor mock_ramp.vendors%rowtype;
  v_hold   uuid;
begin
  select * into v_vendor from mock_ramp.vendors where id = p_vendor_id for update;
  if not found then
    raise exception 'mock_ramp: unknown vendor %', p_vendor_id;
  end if;
  select id into v_hold from mock_ramp.vendor_holds where vendor_id = p_vendor_id and released_at is null;
  if v_hold is not null then
    return v_hold;                                       -- already held: idempotent
  end if;
  insert into mock_ramp.vendor_holds (business_id, vendor_id, reason, placed_by)
  values (v_vendor.business_id, p_vendor_id, p_reason, p_placed_by)
  returning id into v_hold;
  update mock_ramp.bills set is_on_hold = true, payment_date = null
   where vendor_id = p_vendor_id and paid_at is null;
  insert into mock_ramp.audit_events (business_id, event_type, target_type, target_id, payload)
  values (v_vendor.business_id, 'VENDOR_HOLD_PLACED', 'vendor', p_vendor_id, jsonb_build_object('hold_id', v_hold));
  perform mock_ramp.queue_webhook(v_vendor.business_id, 'vendors.updated',
            jsonb_build_object('data', jsonb_build_object('vendor_id', p_vendor_id, 'updated_at', now())));
  return v_hold;
end
$$;

-- POST /developer/v1/vendors/{id}/release. Lifts the hold; payment dates stay cleared.
create function mock_ramp.release_hold(p_vendor_id uuid) returns boolean
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  v_vendor mock_ramp.vendors%rowtype;
  v_n      integer;
begin
  select * into v_vendor from mock_ramp.vendors where id = p_vendor_id for update;
  if not found then
    raise exception 'mock_ramp: unknown vendor %', p_vendor_id;
  end if;
  update mock_ramp.vendor_holds set released_at = now() where vendor_id = p_vendor_id and released_at is null;
  get diagnostics v_n = row_count;
  update mock_ramp.bills set is_on_hold = false where vendor_id = p_vendor_id and is_on_hold;
  if v_n > 0 then
    insert into mock_ramp.audit_events (business_id, event_type, target_type, target_id)
    values (v_vendor.business_id, 'VENDOR_HOLD_RELEASED', 'vendor', p_vendor_id);
    perform mock_ramp.queue_webhook(v_vendor.business_id, 'vendors.updated',
              jsonb_build_object('data', jsonb_build_object('vendor_id', p_vendor_id, 'updated_at', now())));
  end if;
  return v_n > 0;
end
$$;

-- One-shot chaos switches. The API consumes the switch in its own transaction, commits, and
-- then answers 500, so the switch is spent even though the request failed.
create function mock_ramp.consume_switch(p_business_id uuid, p_switch text) returns boolean
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  v_was boolean;
begin
  case p_switch
    when 'fail_next_hold' then
      update mock_ramp.settings set fail_next_hold = false, updated_at = now()
       where business_id = p_business_id and fail_next_hold returning true into v_was;
    when 'fail_next_release' then
      update mock_ramp.settings set fail_next_release = false, updated_at = now()
       where business_id = p_business_id and fail_next_release returning true into v_was;
    else
      raise exception 'mock_ramp: unknown switch %', p_switch;
  end case;
  return coalesce(v_was, false);
end
$$;

-- What GET /developer/v1/vendors/{id}/accounts returns: masked, like Ramp.
create view mock_ramp.vendor_accounts_api as
select a.id, a.vendor_id, a.routing_number, right(a.account_number, 4) as account_number_last4,
       a.account_type, a.bank_name, a.is_active, a.created_at, a.updated_at
  from mock_ramp.vendor_accounts a;

-- Access: the mock service only.
alter table mock_ramp.businesses            enable row level security;
alter table mock_ramp.users                 enable row level security;
alter table mock_ramp.vendors               enable row level security;
alter table mock_ramp.vendor_accounts       enable row level security;
alter table mock_ramp.vendor_contacts       enable row level security;
alter table mock_ramp.bills                 enable row level security;
alter table mock_ramp.vendor_holds          enable row level security;
alter table mock_ramp.audit_events          enable row level security;
alter table mock_ramp.webhook_subscriptions enable row level security;
alter table mock_ramp.webhook_deliveries    enable row level security;
alter table mock_ramp.settings              enable row level security;

revoke all on all tables in schema mock_ramp from public, anon, authenticated;
grant all on all tables in schema mock_ramp to service_role;
grant usage, select on all sequences in schema mock_ramp to service_role;
grant execute on all functions in schema mock_ramp to service_role;
