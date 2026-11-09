-- Self sign-up (spec 0027): every new Supabase account gets a profile and the clerk role.
-- Approver and auditor are still granted only by the team (scripts/db/add_user.py), so signing
-- up never lets anyone release a payment. Neither function can be called by anon or
-- authenticated: they run as the table owner, from the trigger and from this migration.

create function app.welcome_new_user(p_user uuid, p_email text, p_name text) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_name text := btrim(regexp_replace(coalesce(p_name, ''), '[[:cntrl:]]', ' ', 'g'));
begin
  insert into app.user_profiles (user_id, display_name)
  values (p_user, left(coalesce(nullif(v_name, ''), nullif(split_part(coalesce(p_email, ''), '@', 1), ''), 'New user'), 80))
  on conflict (user_id) do nothing;
  insert into app.user_roles (user_id, role) values (p_user, 'clerk')
  on conflict (user_id, role) do nothing;
end
$$;

-- The row is read as JSON, so a stand-in auth.users without Supabase's columns still works.
-- Anonymous sign-ins (off today) never get a role.
create function app.on_auth_user_created() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_row jsonb := to_jsonb(new);
begin
  if coalesce((v_row ->> 'is_anonymous')::boolean, false) then
    return new;
  end if;
  perform app.welcome_new_user(new.id, v_row ->> 'email', v_row -> 'raw_user_meta_data' ->> 'display_name');
  return new;
end
$$;

-- Installs the trigger where auth.users exists (Supabase), and welcomes the accounts that have
-- no role yet. On plain Postgres (CI, laptops) it does nothing; the SQL test runs it against a
-- stand-in auth.users.
create function app.install_sign_up_welcome() returns void
language plpgsql set search_path = '' as $$
begin
  if to_regclass('auth.users') is null then
    return;
  end if;
  create or replace trigger dialback_welcome_new_user
    after insert on auth.users for each row execute function app.on_auth_user_created();
  perform app.welcome_new_user(u.id, to_jsonb(u) ->> 'email', to_jsonb(u) -> 'raw_user_meta_data' ->> 'display_name')
    from auth.users u
   where not exists (select 1 from app.user_roles r where r.user_id = u.id)
     and not coalesce((to_jsonb(u) ->> 'is_anonymous')::boolean, false);
end
$$;

revoke all on function app.welcome_new_user(uuid, text, text), app.on_auth_user_created(), app.install_sign_up_welcome()
  from public, anon, authenticated, service_role;

select app.install_sign_up_welcome();
