-- app: one-click demo accounts act only on demo datasets (spec 0034). Owner: Abhi.
--
-- Judges sign in with shared demo accounts. Whatever the dashboard shows, Postgres refuses any
-- state change or approval those accounts make on a dataset that isn't kind 'demo', and hides
-- every other dataset from them. A demo dataset can't be made against Ramp's sandbox (demo reset
-- and new-dataset --kind demo refuse it), and no dataset's kind can change after it is made.

create table app.demo_accounts (
  user_id    uuid primary key,
  created_at timestamptz not null default now()
);
comment on table app.demo_accounts is
  'Shared demo sign-ins (spec 0034). Added by the team as the owner with scripts/db/add_user.py --demo-only.';

-- Nobody signed in reads or writes it: app.is_demo_account (security definer) answers for them.
alter table app.demo_accounts enable row level security;
revoke all on app.demo_accounts from public, anon, authenticated, service_role;
grant select on app.demo_accounts to service_role;

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

-- What a demo account can see: demo datasets and their cases. Restrictive policies, so they hold
-- whatever permissive policy is added later; everyone else sees what they saw before.
create policy datasets_demo_accounts_see_demo on app.datasets as restrictive for select to authenticated
  using (kind = 'demo' or not (select app.is_demo_account(auth.uid())));
create policy cases_demo_accounts_see_demo on app.cases as restrictive for select to authenticated
  using (not (select app.is_demo_account(auth.uid()))
         or exists (select 1 from app.datasets d where d.id = app.cases.dataset_id and d.kind = 'demo'));

-- A dataset's kind is fixed when it is made: the rule above trusts it, so nothing may turn a
-- dataset into a demo one afterwards (or a demo one into anything else).
create function app.refuse_dataset_kind_change() returns trigger
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
begin
  if new.kind is distinct from old.kind then
    raise exception 'app.datasets: a dataset''s kind can''t change (% to %)', old.kind, new.kind
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end
$$;
create trigger datasets_kind_is_fixed before update of kind on app.datasets
  for each row execute function app.refuse_dataset_kind_change();

revoke all on function app.is_demo_account(uuid), app.refuse_demo_account_off_demo(), app.refuse_dataset_kind_change()
  from public;
grant execute on function app.is_demo_account(uuid) to authenticated, service_role;
