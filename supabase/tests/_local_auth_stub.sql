-- Local stand-in for Supabase's auth.uid(), installed by scripts/db/test.sh and
-- scripts/db/pgtemp.py only when the function is missing (plain Postgres in CI or on a
-- laptop). It reads the same settings PostgREST sets, so tests sign in with:
--   select set_config('request.jwt.claims', '{"sub": "<uuid>", "role": "authenticated"}', true);
-- and it grants what Supabase grants: the API roles can use the auth schema and call auth.uid().
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

create schema if not exists auth;
do $$
begin
  if to_regprocedure('auth.uid()') is null then
    create function auth.uid() returns uuid
    language sql stable as $f$
      select coalesce(
        nullif(current_setting('request.jwt.claim.sub', true), ''),
        nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
      )::uuid
    $f$;
    grant usage on schema auth to anon, authenticated, service_role;
    grant execute on function auth.uid() to anon, authenticated, service_role;
  end if;
end
$$;
