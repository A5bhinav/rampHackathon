-- Dialback: bootstrap. Applied before every owner's folder (see scripts/db/apply.sh).
--
-- Creates the six schemas (one owner each), the Supabase roles when running on a plain
-- Postgres (CI, laptops), and the schema-level grants. Table grants live with each table.
--
--   audit, app, mock_ramp  Abhi
--   ledger                 Inesh
--   voice, lab             Sami

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
end
$$;

create schema if not exists audit;
create schema if not exists app;
create schema if not exists mock_ramp;
create schema if not exists ledger;
create schema if not exists voice;
create schema if not exists lab;

grant usage on schema audit, app, ledger, voice, lab to authenticated, service_role;
grant usage on schema mock_ramp to service_role;
