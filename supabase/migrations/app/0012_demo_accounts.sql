-- app: one-click demo accounts act only on demo datasets (spec 0034). Owner: Abhi.
--
-- Judges sign in with shared demo accounts. Whatever the dashboard shows, Postgres refuses any
-- state change or approval those accounts make on a dataset that isn't kind 'demo', and hides
-- every other dataset from them. Demo datasets exist only through `dialback-core demo reset`,
-- which refuses Ramp's sandbox (spec 0011).

create table app.demo_accounts (
  user_id    uuid primary key,
  created_at timestamptz not null default now()
);
comment on table app.demo_accounts is
  'Shared demo sign-ins (spec 0034). Added by the team as the owner with scripts/db/add_user.py --demo-only.';

alter table app.demo_accounts enable row level security;
create policy demo_accounts_read on app.demo_accounts for select to authenticated using (true);
revoke all on app.demo_accounts from public, anon, authenticated, service_role;
grant select on app.demo_accounts to authenticated, service_role;

create function app.is_demo_account(p_user uuid) returns boolean
language sql stable security definer
set search_path = pg_catalog, pg_temp
as $$
  select p_user is not null and exists (select 1 from app.demo_accounts d where d.user_id = p_user)
$$;

-- Every state change goes through app.cases and every approval into app.releases, so these two
-- triggers cover release, rejection, co-approval and review. The signed-in person is auth.uid();
-- the system's own events run with nobody signed in and are untouched.
create function app.refuse_demo_account_off_demo() returns trigger
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_uid uuid := auth.uid();
begin
  if app.is_demo_account(v_uid)
     and not exists (select 1 from app.datasets d where d.id = new.dataset_id and d.kind = 'demo') then
    raise exception 'demo account %: demo accounts can act only on demo data', v_uid
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end
$$;

create trigger cases_demo_accounts_only_on_demo before update on app.cases
  for each row execute function app.refuse_demo_account_off_demo();
create trigger releases_demo_accounts_only_on_demo before insert on app.releases
  for each row execute function app.refuse_demo_account_off_demo();

-- What a demo account can see: demo datasets and their cases. Everyone else, as before.
drop policy datasets_read on app.datasets;
create policy datasets_read on app.datasets for select to authenticated
  using (kind = 'demo' or not app.is_demo_account(auth.uid()));

drop policy cases_read on app.cases;
create policy cases_read on app.cases for select to authenticated
  using (not app.is_demo_account(auth.uid())
         or exists (select 1 from app.datasets d where d.id = app.cases.dataset_id and d.kind = 'demo'));

revoke all on function app.is_demo_account(uuid), app.refuse_demo_account_off_demo() from public;
grant execute on function app.is_demo_account(uuid) to authenticated, service_role;
