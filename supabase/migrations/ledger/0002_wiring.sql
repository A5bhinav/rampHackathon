-- ledger: the wiring tables. Owner: Inesh. Partner: Abhi. Spec 0024.
--
-- vendor_domains: the vendor's own email domains. Email counts as a source type only from these
--   (spec 0021), so with none, no email counts.
-- change_requests: which ledger document is a vendor's change request (Q46). select_number reads
--   the latest one recorded before the case opened; with none, it refuses (R7).
--
-- Both are append-only, like the rest of the ledger, and carry dataset_id.

create table ledger.vendor_domains (
  dataset_id  uuid not null references app.datasets (id),
  vendor_id   uuid not null references app.vendors (id),
  domain      text not null check (domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'),
  recorded_at timestamptz not null,
  primary key (dataset_id, vendor_id, domain)
);

create table ledger.change_requests (
  id          uuid primary key default gen_random_uuid(),
  dataset_id  uuid not null references app.datasets (id),
  vendor_id   uuid not null references app.vendors (id),
  document_id uuid not null references ledger.documents (id),
  recorded_at timestamptz not null
);
create index change_requests_vendor on ledger.change_requests (dataset_id, vendor_id, recorded_at);

-- A request is one of that vendor's documents, in the same dataset.
create function ledger.check_change_request() returns trigger
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
declare
  d ledger.documents%rowtype;
begin
  select * into d from ledger.documents where id = new.document_id;
  if d.dataset_id <> new.dataset_id or d.vendor_id is distinct from new.vendor_id then
    raise exception 'a change request must be one of that vendor''s documents' using errcode = 'check_violation';
  end if;
  return new;
end
$$;

create trigger vendor_domains_recorded_at  before insert on ledger.vendor_domains  for each row execute function ledger.stamp_recorded_at();
create trigger change_requests_recorded_at before insert on ledger.change_requests for each row execute function ledger.stamp_recorded_at();
create trigger change_requests_document    before insert on ledger.change_requests for each row execute function ledger.check_change_request();

do $$
declare
  t text;
begin
  foreach t in array array['vendor_domains', 'change_requests'] loop
    execute format('create trigger %1$s_no_update before update on ledger.%1$s for each row execute function ledger.refuse_mutation()', t);
    execute format('create trigger %1$s_no_delete before delete on ledger.%1$s for each row execute function ledger.refuse_mutation()', t);
    execute format('create trigger %1$s_no_truncate before truncate on ledger.%1$s for each statement execute function ledger.refuse_mutation()', t);
  end loop;
end
$$;

alter table ledger.vendor_domains  enable row level security;
alter table ledger.change_requests enable row level security;
create policy vendor_domains_read  on ledger.vendor_domains  for select to authenticated using (true);
create policy change_requests_read on ledger.change_requests for select to authenticated using (true);

revoke all on ledger.vendor_domains, ledger.change_requests from public, anon, authenticated, service_role;
grant select on ledger.vendor_domains, ledger.change_requests to authenticated, service_role;
grant insert on ledger.vendor_domains, ledger.change_requests to service_role;
revoke execute on all functions in schema ledger from public;
