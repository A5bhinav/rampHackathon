-- 013: a new account gets a profile and the clerk role, and nothing more (spec 0027).
-- Plain Postgres has no auth.users, so the migration installed no trigger. This test makes a
-- stand-in auth.users with the columns Supabase has, runs the same installer, and rolls back.
begin;
\i supabase/tests/_helpers.sql

do $$
begin
  assert not exists (select 1 from pg_trigger where tgname = 'dialback_welcome_new_user'),
    'without auth.users, the migration installs no trigger';
end
$$;

create table auth.users (id uuid primary key, email text, raw_user_meta_data jsonb, is_anonymous boolean not null default false);

do $$
declare
  approver uuid := gen_random_uuid();
  bare     uuid := gen_random_uuid();
  named    uuid := gen_random_uuid();
  unnamed  uuid := gen_random_uuid();
begin
  -- Accounts that exist before the installer runs: one the team already set up, one with no role.
  insert into auth.users (id, email) values (approver, 'riley@example.test'), (bare, 'pat.lee@example.test');
  insert into app.user_profiles (user_id, display_name) values (approver, 'Riley Approver');
  insert into app.user_roles (user_id, role) values (approver, 'approver');

  perform app.install_sign_up_welcome();
  perform app.install_sign_up_welcome();  -- running it again is harmless

  assert (select array_agg(role::text order by role::text) from app.user_roles where user_id = approver) = array['approver'],
    'an account with a role is left alone';
  assert (select display_name from app.user_profiles where user_id = approver) = 'Riley Approver', 'its name too';
  assert (select array_agg(role::text) from app.user_roles where user_id = bare) = array['clerk'],
    'an account with no role becomes a clerk';
  assert (select display_name from app.user_profiles where user_id = bare) = 'pat.lee', 'named after its email';

  -- Sign-ups after the installer: the name the person gave, or the email's local part.
  insert into auth.users (id, email, raw_user_meta_data) values (named, 'sam@example.test', '{"display_name": "  Sam Clerk "}');
  insert into auth.users (id, email, raw_user_meta_data) values (unnamed, 'jo@example.test', '{"display_name": "   "}');
  assert (select display_name from app.user_profiles where user_id = named) = 'Sam Clerk', 'the given name, trimmed';
  assert (select display_name from app.user_profiles where user_id = unnamed) = 'jo', 'a blank name falls back to the email';
  assert (select array_agg(role::text) from app.user_roles where user_id = named) = array['clerk'], 'clerk, and only clerk';
  assert (select count(*) from app.user_roles where user_id in (named, unnamed) and role <> 'clerk') = 0,
    'sign-up never grants approver or auditor';

  -- Welcoming someone twice changes nothing they already have.
  perform app.welcome_new_user(approver, 'riley@example.test', 'Someone Else');
  assert (select display_name from app.user_profiles where user_id = approver) = 'Riley Approver', 'the name stays';
  assert (select array_agg(role::text order by role::text) from app.user_roles where user_id = approver) = array['approver', 'clerk'],
    'an existing approver who is welcomed only gains clerk';

  -- A very long name is cut to 80 characters; control characters become spaces. Someone who
  -- calls Supabase directly skips the form's checks, so these are the ones that hold.
  declare
    long_name uuid := gen_random_uuid();
    odd_name  uuid := gen_random_uuid();
    no_meta   uuid := gen_random_uuid();
    anonymous uuid := gen_random_uuid();
  begin
    perform app.welcome_new_user(long_name, 'x@example.test', repeat('n', 200));
    assert (select display_name from app.user_profiles where user_id = long_name) = repeat('n', 80), 'cut to 80';
    insert into auth.users (id, email, raw_user_meta_data) values (odd_name, 'o@example.test', jsonb_build_object('display_name', E'Ann\nBell\u0001'));
    assert (select display_name from app.user_profiles where user_id = odd_name) = 'Ann Bell', 'no control characters';
    insert into auth.users (id, email) values (no_meta, 'quinn@example.test');
    assert (select display_name from app.user_profiles where user_id = no_meta) = 'quinn', 'no metadata at all';
    assert (select array_agg(role::text) from app.user_roles where user_id = no_meta) = array['clerk'], 'still a clerk';
    insert into auth.users (id, email, is_anonymous) values (anonymous, null, true);
    assert not exists (select 1 from app.user_roles where user_id = anonymous), 'an anonymous sign-in gets no role';
    assert not exists (select 1 from app.user_profiles where user_id = anonymous), 'nor a profile';
  end;
end
$$;

-- Only the table owner runs these: neither the dashboard's users nor the API roles can.
do $$
declare
  r text;
  f text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    foreach f in array array['app.welcome_new_user(uuid, text, text)', 'app.on_auth_user_created()', 'app.install_sign_up_welcome()'] loop
      assert not has_function_privilege(r, f, 'execute'), format('%s must not run %s', r, f);
    end loop;
  end loop;
  set local role authenticated;
  perform pg_temp.expect_error($q$ select app.welcome_new_user(gen_random_uuid(), 'a@example.test', 'A') $q$, '42501');
  reset role;
end
$$;

rollback;
