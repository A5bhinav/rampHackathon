-- ledger: the Trust Ledger. Owner: Inesh. Partner: Abhi. Spec 0013.
--
-- Where every phone number in a vendor's history came from, and which number a case will
-- call. The database enforces the rules the selection rule leans on, so a bug in the Python
-- can't break them:
--
--   * provenance comes from custody, never from a model: an observation's source_type and
--     seen_at are copied from its document by a trigger (ADR-13, R9);
--   * documents, observations and selections are append-only, and every observation
--     records when we learned it, so a selection can be replayed (R6);
--   * no full bank account number is ever stored: routing, last four, keyed fingerprint,
--     and no quote (ADR-8);
--   * a stored selection agrees with its own selection.schema.json document (spec 0018).
--
-- Every table carries dataset_id, like app, so a demo reset or a lab scenario starts fresh.

-- Types -------------------------------------------------------------------------------

create type ledger.source_type as enum ('email', 'contract', 'vendor_portal', 'listing', 'paper_mail', 'vendor_master');
comment on type ledger.source_type is
  'How a document reached us (ADR-13). All email is one source type. vendor_master is the old "number on file": shown, never counted.';

create type ledger.document_kind    as enum ('email', 'invoice', 'contract', 'w9', 'listing', 'portal_entry', 'letter', 'vendor_master_record', 'other');
create type ledger.observation_kind as enum ('phone', 'bank_account', 'email', 'domain');
create type ledger.line_role        as enum ('main', 'ar', 'billing', 'fax', 'unknown');
create type ledger.party            as enum ('vendor', 'customer', 'bank', 'unknown');
create type ledger.visibility       as enum ('visible', 'white', 'tiny', 'off_page');
create type ledger.contact_status   as enum ('eligible', 'excluded');
create type ledger.selection_outcome as enum ('picked', 'no_provable_number');

-- Documents: one source file and how it reached us ----------------------------------------

create table ledger.documents (
  id                 uuid primary key default gen_random_uuid(),
  dataset_id         uuid not null references app.datasets (id),
  vendor_id          uuid references app.vendors (id),           -- null until entity resolution attaches it
  kind               ledger.document_kind not null,
  sha256             text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  custody_channel    ledger.source_type not null,
  received_at        timestamptz not null,                       -- our clock; the only time the rule trusts
  claimed_date       date,                                       -- printed on the document; shown, never trusted
  sender             text,
  thread_id          text,
  subject            text,
  parent_document_id uuid references ledger.documents (id),     -- an attachment's email
  storage_path       text,
  recorded_at        timestamptz not null
);
create index documents_vendor on ledger.documents (dataset_id, vendor_id, received_at);
create index documents_thread on ledger.documents (dataset_id, thread_id);

comment on column ledger.documents.received_at is 'Our own receive time. Never a printed date or an email''s Date: header.';
comment on column ledger.documents.claimed_date is 'The date the document claims. Displayed only; the selection rule never reads it.';

-- Observations: "value X was seen in document D" ------------------------------------------

create table ledger.observations (
  id                uuid primary key default gen_random_uuid(),
  dataset_id        uuid not null references app.datasets (id),
  document_id       uuid not null references ledger.documents (id),
  vendor_id         uuid references app.vendors (id),
  kind              ledger.observation_kind not null,
  value_normalized  text not null,
  role              ledger.line_role not null default 'unknown',
  party             ledger.party not null default 'unknown',
  quote             text,                                        -- the exact visible text the value was parsed from
  visibility        ledger.visibility not null,
  source_type       ledger.source_type not null,                 -- set from the document by trigger
  seen_at           timestamptz not null,                        -- set from the document by trigger
  extractor_version text not null,
  recorded_at       timestamptz not null,                        -- when we learned it (app.now)
  constraint phone_is_e164 check (kind <> 'phone' or value_normalized ~ '^\+[1-9][0-9]{1,14}$'),
  constraint bank_account_is_masked check (kind <> 'bank_account' or value_normalized ~ '^[0-9]{9}:[0-9]{4}:[0-9a-f]{64}$'),
  constraint bank_account_is_not_quoted check (kind <> 'bank_account' or quote is null)
);
create index observations_vendor on ledger.observations (dataset_id, vendor_id, kind, value_normalized);
create index observations_document on ledger.observations (document_id);

comment on column ledger.observations.value_normalized is
  'phone: E.164. bank_account: routing:last4:keyed fingerprint, never the number (ADR-8). email, domain: lowercased.';

-- Contact points: one number per vendor and its lineage summary ---------------------------

create table ledger.contact_points (
  id               uuid primary key default gen_random_uuid(),
  dataset_id       uuid not null references app.datasets (id),
  vendor_id        uuid not null references app.vendors (id),
  e164             text not null check (e164 ~ '^\+[1-9][0-9]{1,14}$'),
  first_seen_at    timestamptz not null,
  source_types     ledger.source_type[] not null default '{}',
  status           ledger.contact_status not null,
  exclusion_reason text,
  updated_at       timestamptz not null default now(),
  unique (dataset_id, vendor_id, e164),
  constraint excluded_has_reason check (status = 'eligible' or exclusion_reason is not null)
);

comment on table ledger.contact_points is
  'A summary for the dashboard, kept up to date by ingestion. A selection replays from observations, never from this table.';

-- Number selections: which number a case will call, and why ---------------------------------

create table ledger.number_selections (
  id               uuid primary key,                             -- the document's number_selection_id
  dataset_id       uuid not null references app.datasets (id),
  case_id          uuid not null references app.cases (id),
  vendor_id        uuid not null references app.vendors (id),
  contact_point_id uuid references ledger.contact_points (id),
  outcome          ledger.selection_outcome not null,
  rule_version     text not null,
  inputs_sha256    text not null check (inputs_sha256 ~ '^[0-9a-f]{64}$'),
  selection        jsonb not null,                              -- selection.schema.json (spec 0018)
  recorded_at      timestamptz not null,
  constraint pick_has_contact_point check ((outcome = 'picked') = (contact_point_id is not null)),
  constraint matches_its_document check (
        selection ->> 'number_selection_id' = id::text
    and selection ->> 'case_id' = case_id::text
    and selection ->> 'dataset_id' = dataset_id::text
    and selection ->> 'vendor_id' = vendor_id::text
    and selection ->> 'outcome' = outcome::text
    and selection ->> 'rule_version' = rule_version
    and selection ->> 'inputs_sha256' = inputs_sha256
    and (selection ->> 'contact_point_id') is not distinct from contact_point_id::text)
);
create index number_selections_case on ledger.number_selections (case_id, recorded_at);

-- Triggers ---------------------------------------------------------------------------------

-- recorded_at is the dataset's clock, whatever the insert says.
create function ledger.stamp_recorded_at() returns trigger
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
begin
  new.recorded_at := app.now(new.dataset_id);
  return new;
end
$$;

-- An attachment arrives the way its email did.
create function ledger.check_attachment_custody() returns trigger
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
declare
  p ledger.documents%rowtype;
begin
  if new.parent_document_id is null then
    return new;
  end if;
  select * into p from ledger.documents where id = new.parent_document_id;
  if p.dataset_id <> new.dataset_id or p.custody_channel <> new.custody_channel or p.received_at <> new.received_at then
    raise exception 'an attachment must have its parent document''s dataset, custody channel and receive time'
      using errcode = 'check_violation';
  end if;
  return new;
end
$$;

-- Provenance comes from custody, never from the insert (and so never from a model).
create function ledger.observation_from_custody() returns trigger
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
declare
  d ledger.documents%rowtype;
begin
  select * into d from ledger.documents where id = new.document_id;
  if d.dataset_id <> new.dataset_id then
    raise exception 'an observation must be in its document''s dataset' using errcode = 'check_violation';
  end if;
  new.source_type := d.custody_channel;
  new.seen_at := d.received_at;
  return new;
end
$$;

-- A selection agrees with the case and the contact point it names: same dataset and vendor,
-- and the number in the document is the contact point's number (review of spec 0013).
create function ledger.check_selection_refs() returns trigger
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
declare
  c app.cases%rowtype;
  p ledger.contact_points%rowtype;
begin
  select * into c from app.cases where id = new.case_id;
  if c.dataset_id <> new.dataset_id or c.vendor_id <> new.vendor_id then
    raise exception 'a selection must be in its case''s dataset and for its case''s vendor' using errcode = 'check_violation';
  end if;
  if new.contact_point_id is not null then
    select * into p from ledger.contact_points where id = new.contact_point_id;
    if p.dataset_id <> new.dataset_id or p.vendor_id <> new.vendor_id or p.e164 is distinct from new.selection ->> 'e164' then
      raise exception 'a selection''s contact point must be its vendor''s, with the number the selection names'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end
$$;

-- A contact point's identity never changes once written; only its summary does.
create function ledger.freeze_contact_point() returns trigger
language plpgsql as $$
begin
  if new.id <> old.id or new.dataset_id <> old.dataset_id or new.vendor_id <> old.vendor_id or new.e164 <> old.e164 then
    raise exception 'a contact point''s dataset, vendor and number are fixed' using errcode = 'insufficient_privilege';
  end if;
  return new;
end
$$;

create function ledger.refuse_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'ledger.% is append-only (R6): % refused', tg_table_name, tg_op
    using errcode = 'insufficient_privilege';
end
$$;

create trigger documents_recorded_at         before insert on ledger.documents         for each row execute function ledger.stamp_recorded_at();
create trigger documents_attachment_custody  before insert on ledger.documents         for each row execute function ledger.check_attachment_custody();
create trigger observations_recorded_at      before insert on ledger.observations      for each row execute function ledger.stamp_recorded_at();
create trigger observations_custody          before insert on ledger.observations      for each row execute function ledger.observation_from_custody();
create trigger number_selections_recorded_at before insert on ledger.number_selections for each row execute function ledger.stamp_recorded_at();
create trigger contact_points_touch          before update on ledger.contact_points    for each row execute function app.touch_updated_at();
create trigger number_selections_refs        before insert on ledger.number_selections for each row execute function ledger.check_selection_refs();
create trigger contact_points_frozen         before update on ledger.contact_points    for each row execute function ledger.freeze_contact_point();

do $$
declare
  t text;
begin
  foreach t in array array['documents', 'observations', 'number_selections'] loop
    execute format('create trigger %1$s_no_update before update on ledger.%1$s for each row execute function ledger.refuse_mutation()', t);
    execute format('create trigger %1$s_no_delete before delete on ledger.%1$s for each row execute function ledger.refuse_mutation()', t);
    execute format('create trigger %1$s_no_truncate before truncate on ledger.%1$s for each statement execute function ledger.refuse_mutation()', t);
  end loop;
end
$$;

-- Access -----------------------------------------------------------------------------------
-- Everyone signed in reads; the service role (the core and its ledger package) inserts and
-- keeps contact_points up to date; nobody updates or deletes the append-only tables.

alter table ledger.documents         enable row level security;
alter table ledger.observations      enable row level security;
alter table ledger.contact_points    enable row level security;
alter table ledger.number_selections enable row level security;

create policy documents_read         on ledger.documents         for select to authenticated using (true);
create policy observations_read      on ledger.observations      for select to authenticated using (true);
create policy contact_points_read    on ledger.contact_points    for select to authenticated using (true);
create policy number_selections_read on ledger.number_selections for select to authenticated using (true);

revoke all on all tables in schema ledger from public, anon, authenticated, service_role;
grant select on all tables in schema ledger to authenticated, service_role;
grant insert on ledger.documents, ledger.observations, ledger.number_selections, ledger.contact_points to service_role;
grant update (first_seen_at, source_types, status, exclusion_reason, updated_at) on ledger.contact_points to service_role;

revoke execute on all functions in schema ledger from public;
