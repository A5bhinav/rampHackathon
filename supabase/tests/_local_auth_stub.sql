-- Local stand-in for Supabase's auth.uid(), installed by scripts/db/test.sh only when the
-- function is missing (plain Postgres in CI or on a laptop). It reads the same settings
-- PostgREST sets, so tests sign in with:
--   select set_config('request.jwt.claims', '{"sub": "<uuid>", "role": "authenticated"}', true);
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
  end if;
end
$$;
