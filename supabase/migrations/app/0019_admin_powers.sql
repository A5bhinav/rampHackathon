-- app: what an admin can do, and the organisation chain that records it (spec 0095). Owner: Abhi.
--
-- D1: an admin counts as every role. has_role is the one place every guard reads a role from
-- (eval_guard, release_case, the review guards), so redefining it here is enough; the R5 checks
-- (changer, reviewer, first approver) sit beside the role check and are untouched.
-- D7: a person whose access is off holds no role at all, so the database refuses them too, not
-- only the core.
-- D5: whoever set a person's Ramp link counts as a changer of every change made through it, so
-- an admin can't link someone else to their own Ramp identity and release the change (R5).
-- D2-D8: each admin power is one security-definer door that reads auth.uid() (ADR-9), checks the
-- caller holds admin itself, refuses demo accounts and turned-off people, needs a reason, and
-- appends one event to the organisation chain (R6). A refusal changes and logs nothing.
-- create or replace keeps has_role's grants (see 0002).

-- D7: access off keeps the roles; has_role and the core refuse the person.
alter table app.user_profiles add column disabled_at timestamptz;
alter table app.user_profiles add column disabled_by uuid;

create or replace function app.has_role(p_user uuid, p_role app.user_role) returns boolean
language sql stable
set search_path = pg_catalog, pg_temp
as $$
  select exists (select 1 from app.user_roles r
                  where r.user_id = p_user and (r.role = p_role or r.role = 'admin'::app.user_role))
     and not exists (select 1 from app.user_profiles p where p.user_id = p_user and p.disabled_at is not null)
$$;

comment on function app.has_role(uuid, app.user_role) is
  'True when the person holds the role, or holds admin, which counts as every role, and their access is on (spec 0095, D1, D7).';

-- D5: the admin who set the Ramp link; null when the team set it (add_user.py, demo reset from the shell).
alter table app.user_profiles add column ramp_linked_by uuid;
alter table app.bank_change_events add column changed_by_linker uuid;
comment on column app.bank_change_events.changed_by_linker is
  'Who had set the changer''s Ramp link when the change was recorded. A changer too (spec 0095, D5).';

create function app.record_ramp_linker() returns trigger
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_profile app.user_profiles%rowtype;
begin
  if new.changed_by is null then
    return new;
  end if;
  -- Read the changer's link and its linker together, locked until this change is recorded. If the
  -- person no longer holds the Ramp user the change came from (relinked since detection looked them
  -- up), the author is unknown, so the case needs two approvers (ADR-10): fail closed.
  select * into v_profile from app.user_profiles p where p.user_id = new.changed_by for share;
  if new.changed_by_ramp_user_id is not null and v_profile.ramp_user_id is distinct from new.changed_by_ramp_user_id then
    new.changed_by := null;
    return new;
  end if;
  new.changed_by_linker := v_profile.ramp_linked_by;
  return new;
end
$$;
create trigger bank_change_events_record_linker before insert on app.bank_change_events
  for each row execute function app.record_ramp_linker();

create function app.linker_is_a_changer() returns trigger
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_linker uuid := (select e.changed_by_linker from app.bank_change_events e where e.id = new.change_event_id);
begin
  if v_linker is not null and not (v_linker = any (new.changer_ids)) then
    new.changer_ids := new.changer_ids || v_linker;
  end if;
  return new;
end
$$;
create trigger cases_linker_is_a_changer before insert or update of changer_ids, change_event_id on app.cases
  for each row execute function app.linker_is_a_changer();
revoke all on function app.record_ramp_linker(), app.linker_is_a_changer() from public, anon, authenticated, service_role;

-- The core's server role writes profiles (demo reset links) and change events (detection), so the
-- columns this spec relies on are guarded from it: it may not grant or remove admin, turn access off
-- or on, set a linker, or rewrite a change's linker. Only the doors (as the owner) and the team's
-- add_user.py (the owner) change them. A shell reset that relinks someone clears the linker (D5).
create function app.refuse_server_role_admin_writes() returns trigger
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
begin
  if current_user <> 'service_role' then
    return coalesce(new, old);
  end if;
  if tg_table_name = 'user_roles' then
    if (case when tg_op = 'DELETE' then old.role else new.role end) = 'admin'
       or (tg_op = 'UPDATE' and old.role = 'admin') then
      raise exception 'the server role can''t grant or remove admin (spec 0095)' using errcode = 'insufficient_privilege';
    end if;
  elsif tg_table_name = 'user_profiles' then
    if tg_op = 'INSERT' then
      if new.disabled_at is not null or new.ramp_linked_by is not null then
        raise exception 'the server role can''t set access or a linker (spec 0095)' using errcode = 'insufficient_privilege';
      end if;
    else
      if new.disabled_at is distinct from old.disabled_at or new.disabled_by is distinct from old.disabled_by then
        raise exception 'the server role can''t turn access on or off (spec 0095)' using errcode = 'insufficient_privilege';
      end if;
      if new.ramp_linked_by is distinct from old.ramp_linked_by
         and not (new.ramp_linked_by is null and new.ramp_user_id is distinct from old.ramp_user_id) then
        raise exception 'the server role can only clear a linker while it relinks the person (spec 0095)'
          using errcode = 'insufficient_privilege';
      end if;
    end if;
  elsif tg_table_name = 'bank_change_events' then
    if new.changed_by_linker is distinct from old.changed_by_linker then
      raise exception 'the server role can''t rewrite who linked a changer (spec 0095)' using errcode = 'insufficient_privilege';
    end if;
  end if;
  return coalesce(new, old);
end
$$;
create trigger user_roles_server_role_guard before insert or update or delete on app.user_roles
  for each row execute function app.refuse_server_role_admin_writes();
create trigger user_profiles_server_role_guard before insert or update on app.user_profiles
  for each row execute function app.refuse_server_role_admin_writes();
create trigger bank_change_events_server_role_guard before update on app.bank_change_events
  for each row execute function app.refuse_server_role_admin_writes();
revoke all on function app.refuse_server_role_admin_writes() from public, anon, authenticated, service_role;

-- D8: the organisation chain, made once here.
create table app.org_settings (
  singleton boolean primary key default true check (singleton),
  chain_id  uuid not null references audit.chains (id)
);
comment on table app.org_settings is 'One row: the organisation chain that admin actions append to (spec 0095, D8).';
with c as (insert into audit.chains (label) values ('org') returning id)
insert into app.org_settings (chain_id) select id from c;

alter table app.org_settings enable row level security;
revoke all on app.org_settings from public, anon, authenticated, service_role;
grant select on app.org_settings to service_role;

-- Internal helpers: callable only from the doors below (they run as the owner).

create function app.admin_caller() returns uuid
language plpgsql stable security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_uid uuid := auth.uid();
begin
  if v_uid is null then
    raise exception 'admin: sign in first; admin actions are never made with a server key (ADR-9)'
      using errcode = 'insufficient_privilege';
  end if;
  if app.is_demo_account(v_uid) then
    raise exception 'admin: a demo account can''t act as an admin (spec 0034)' using errcode = 'insufficient_privilege';
  end if;
  if exists (select 1 from app.user_profiles p where p.user_id = v_uid and p.disabled_at is not null) then
    raise exception 'admin: your access is turned off' using errcode = 'insufficient_privilege';
  end if;
  if not exists (select 1 from app.user_roles r where r.user_id = v_uid and r.role = 'admin') then
    raise exception 'admin: the admin role is required' using errcode = 'insufficient_privilege';
  end if;
  return v_uid;
end
$$;

comment on function app.admin_caller() is
  'The signed-in admin, or insufficient_privilege. The core calls it before running a demo command (spec 0095, D9).';

create function app.admin_reason(p_reason text) returns text
language plpgsql immutable
set search_path = pg_catalog, pg_temp
as $$
begin
  if length(btrim(coalesce(p_reason, ''))) = 0 then
    raise exception 'admin: a written reason is required' using errcode = 'check_violation';
  end if;
  if length(btrim(p_reason)) > 2000 then
    raise exception 'admin: keep the reason under 2000 characters' using errcode = 'check_violation';
  end if;
  return btrim(p_reason);
end
$$;

create function app.admin_name(p_user uuid) returns text
language sql stable security definer
set search_path = pg_catalog, pg_temp
as $$
  select coalesce((select display_name from app.user_profiles where user_id = p_user), 'Unknown person')
$$;

create function app.admin_append(p_actor uuid, p_type text, p_subject uuid, p_detail jsonb, p_reason text) returns bigint
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_chain uuid := (select chain_id from app.org_settings);
  v_seq   bigint;
begin
  select a.seq into v_seq from audit.append_evidence(v_chain, jsonb_build_object(
    'v', 1, 'chain_id', v_chain, 'event_id', gen_random_uuid(), 'type', p_type,
    'actor', case when p_actor is null then jsonb_build_object('kind', 'system', 'id', null)
                  else jsonb_build_object('kind', 'person', 'id', p_actor) end,
    'occurred_at', app.rfc3339(now()), 'subject_user_id', p_subject,
    'detail', coalesce(p_detail, '{}'::jsonb), 'reason', p_reason)) a;
  return v_seq;
end
$$;

-- D4: Dialback always keeps an admin whose access is on.
create function app.admin_keep_one() returns void
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  if not exists (select 1 from app.user_roles r left join app.user_profiles p on p.user_id = r.user_id
                  where r.role = 'admin' and p.disabled_at is null and not app.is_demo_account(r.user_id)) then
    raise exception 'admin: Dialback keeps one admin whose access is on, so the last admin can''t be removed or turned off'
      using errcode = 'check_violation';
  end if;
end
$$;

-- One person as an admin sees them (the contract's PersonAdminView). auth.users exists on
-- Supabase only; it is read as JSON so a plain Postgres or a stand-in table works too.
create function app.admin_person(p_user uuid) returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_profile app.user_profiles%rowtype;
  v_auth    jsonb;
begin
  select * into v_profile from app.user_profiles where user_id = p_user;
  if not found then
    return null;
  end if;
  if to_regclass('auth.users') is not null then
    execute 'select to_jsonb(u) from auth.users u where u.id = $1' into v_auth using p_user;
  end if;
  return jsonb_build_object(
    'id', p_user,
    'display_name', v_profile.display_name,
    'email', v_auth ->> 'email',
    'roles', coalesce((select jsonb_agg(r.role order by r.role) from app.user_roles r where r.user_id = p_user), '[]'::jsonb),
    'ramp_user_id', v_profile.ramp_user_id,
    'demo_only', app.is_demo_account(p_user),
    'access_enabled', v_profile.disabled_at is null,
    'last_sign_in_at', app.rfc3339((v_auth ->> 'last_sign_in_at')::timestamptz));
end
$$;

create function app.admin_subject(p_user uuid) returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v jsonb := app.admin_person(p_user);
begin
  if v is null then
    raise exception 'admin: no person %', p_user using errcode = 'no_data_found';
  end if;
  return v;
end
$$;

-- The doors -------------------------------------------------------------------------------------

create function app.admin_people() returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  perform app.admin_caller();
  return coalesce((select jsonb_agg(app.admin_person(p.user_id) order by p.display_name, p.user_id)
                     from app.user_profiles p), '[]'::jsonb);
end
$$;

create function app.admin_set_role(p_user uuid, p_role app.user_role, p_grant boolean, p_reason text) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_actor  uuid;
  v_reason text;
  v_name   text;
  v_has    boolean;
begin
  perform pg_advisory_xact_lock(hashtext('dialback.admins'));  -- D4: one admin change at a time,
  v_actor  := app.admin_caller();                              -- and the caller checked after the lock
  v_reason := app.admin_reason(p_reason);
  v_name := app.admin_subject(p_user) ->> 'display_name';
  v_has  := exists (select 1 from app.user_roles where user_id = p_user and role = p_role);
  if p_grant is null then
    raise exception 'admin: say whether to grant or take away the role' using errcode = 'check_violation';
  elsif p_grant then
    if v_has then
      raise exception 'admin: % already has the % role', v_name, p_role using errcode = 'check_violation';
    end if;
    if p_role = 'admin' and app.is_demo_account(p_user) then
      raise exception 'admin: % is a demo account, and a demo account can''t be an admin (spec 0034)', v_name
        using errcode = 'check_violation';
    end if;
    insert into app.user_roles (user_id, role) values (p_user, p_role);
  else
    if not v_has then
      raise exception 'admin: % doesn''t have the % role', v_name, p_role using errcode = 'check_violation';
    end if;
    delete from app.user_roles where user_id = p_user and role = p_role;
    perform app.admin_keep_one();
  end if;
  perform app.admin_append(v_actor, case when p_grant then 'role_granted' else 'role_revoked' end, p_user,
                           jsonb_build_object('role', p_role), v_reason);
  return app.admin_person(p_user);
end
$$;

-- D5: never your own link (it decides who entered a change, R5), and one person per Ramp user.
create function app.admin_set_ramp_link(p_user uuid, p_ramp_user_id text, p_reason text) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_actor    uuid;
  v_reason   text;
  v_name     text;
  v_ramp     text := nullif(btrim(coalesce(p_ramp_user_id, '')), '');
  v_previous text;
  v_holder   uuid;
begin
  perform pg_advisory_xact_lock(hashtext('dialback.admins'));
  v_actor  := app.admin_caller();
  v_reason := app.admin_reason(p_reason);
  v_name := app.admin_subject(p_user) ->> 'display_name';
  if p_user = v_actor then
    raise exception 'admin: you can''t change your own Ramp link; it decides who entered a change (R5)'
      using errcode = 'check_violation';
  end if;
  if length(v_ramp) > 200 then
    raise exception 'admin: a Ramp user id is at most 200 characters' using errcode = 'check_violation';
  end if;
  select ramp_user_id into v_previous from app.user_profiles where user_id = p_user for update;
  if v_previous is not distinct from v_ramp then
    raise exception 'admin: % is already linked that way', v_name using errcode = 'check_violation';
  end if;
  if v_ramp is not null then
    select user_id into v_holder from app.user_profiles where ramp_user_id = v_ramp and user_id <> p_user;
    if found then
      raise exception 'admin: that Ramp user is already linked to %', app.admin_name(v_holder) using errcode = 'check_violation';
    end if;
  end if;
  update app.user_profiles set ramp_user_id = v_ramp, ramp_linked_by = case when v_ramp is null then null else v_actor end
   where user_id = p_user;
  perform app.admin_append(v_actor, 'ramp_link_set', p_user,
                           jsonb_build_object('ramp_user_id', v_ramp, 'previous_ramp_user_id', v_previous), v_reason);
  return app.admin_person(p_user);
end
$$;

create function app.admin_set_access(p_user uuid, p_enabled boolean, p_reason text) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_actor   uuid;
  v_reason  text;
  v_name    text;
  v_enabled boolean;
begin
  perform pg_advisory_xact_lock(hashtext('dialback.admins'));
  v_actor  := app.admin_caller();
  v_reason := app.admin_reason(p_reason);
  v_name := app.admin_subject(p_user) ->> 'display_name';
  if p_enabled is null then
    raise exception 'admin: say whether to turn access on or off' using errcode = 'check_violation';
  end if;
  if p_user = v_actor then
    raise exception 'admin: you can''t change your own access' using errcode = 'check_violation';
  end if;
  select disabled_at is null into v_enabled from app.user_profiles where user_id = p_user for update;
  if v_enabled = p_enabled then
    raise exception 'admin: % already has access turned %', v_name, case when p_enabled then 'on' else 'off' end
      using errcode = 'check_violation';
  end if;
  update app.user_profiles
     set disabled_at = case when p_enabled then null else now() end,
         disabled_by = case when p_enabled then null else v_actor end
   where user_id = p_user;
  if not p_enabled then
    perform app.admin_keep_one();
  end if;
  perform app.admin_append(v_actor, case when p_enabled then 'access_enabled' else 'access_disabled' end, p_user,
                           '{}'::jsonb, v_reason);
  return app.admin_person(p_user);
end
$$;

-- The organisation chain, newest first, with whether it verifies (the contract's AdminLog).
create function app.admin_log(p_limit integer default 100) returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_chain uuid := (select chain_id from app.org_settings);
  v_limit integer := least(greatest(coalesce(p_limit, 100), 1), 500);
  v_check record;
  v_tip   record;
begin
  perform app.admin_caller();
  select * into v_check from audit.verify_chain(v_chain);
  select * into v_tip from audit.chain_tip(v_chain);
  return jsonb_build_object(
    'chain', jsonb_build_object('chain_id', v_chain, 'ok', v_check.ok, 'checked', v_check.checked,
                                'first_bad_seq', v_check.first_bad_seq, 'detail', v_check.detail,
                                'tip_seq', v_tip.seq, 'tip_hash', v_tip.hash),
    'events', coalesce((
      select jsonb_agg(jsonb_build_object(
               'seq', e.seq, 'type', e.type, 'occurred_at', app.rfc3339(e.occurred_at),
               'actor', case when e.actor_id is null then null
                             else jsonb_build_object('id', e.actor_id, 'display_name', app.admin_name(e.actor_id)) end,
               'subject', case when b.body ->> 'subject_user_id' is null then null
                               else jsonb_build_object('id', b.body ->> 'subject_user_id',
                                                       'display_name', app.admin_name((b.body ->> 'subject_user_id')::uuid)) end,
               'detail', coalesce(b.body -> 'detail', '{}'::jsonb),
               'reason', coalesce(b.body ->> 'reason', '')) order by e.seq desc)
        from (select * from audit.evidence_events where chain_id = v_chain order by seq desc limit v_limit) e
        cross join lateral (select e.body_jcs::jsonb as body) b), '[]'::jsonb));
end
$$;

-- D9: the core records a demo command as the signed-in admin before it runs it, so a command
-- that fails partway is still on the chain, and this call is also the admin check.
create function app.admin_record_demo(p_type text, p_detail jsonb, p_reason text) returns bigint
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_actor   uuid;
  v_reason  text;
  v_keys    text[];
  v_current uuid;
  v_ok      boolean;
begin
  perform pg_advisory_xact_lock(hashtext('dialback.admins'));
  v_actor  := app.admin_caller();
  v_reason := app.admin_reason(p_reason);
  if p_type is null or p_type not in ('demo_reset', 'demo_chaos', 'demo_tamper') then
    raise exception 'admin: % is not a demo action', p_type using errcode = 'check_violation';
  end if;
  -- Exactly the keys admin-event.schema.json allows for the type; a dataset must be the current demo.
  v_keys := case when jsonb_typeof(p_detail) = 'object' then (select array_agg(k order by k) from jsonb_object_keys(p_detail) k) end;
  v_current := (select d.id from app.datasets d where d.kind = 'demo' and d.retired_at is null order by d.created_at desc limit 1);
  v_ok := case p_type
       when 'demo_reset'  then v_keys = array['linked_clerk_user_id']
                               and (jsonb_typeof(p_detail -> 'linked_clerk_user_id') = 'null'
                                    or exists (select 1 from app.user_profiles where user_id::text = p_detail ->> 'linked_clerk_user_id'))
       when 'demo_chaos'  then v_keys = array['dataset_id', 'enabled'] and jsonb_typeof(p_detail -> 'enabled') = 'boolean'
                               and p_detail ->> 'dataset_id' = v_current::text
       when 'demo_tamper' then v_keys = array['dataset_id'] and p_detail ->> 'dataset_id' = v_current::text
     end;
  if not coalesce(v_ok, false) then
    raise exception 'admin: that isn''t a valid % for the current demo', p_type using errcode = 'check_violation';
  end if;
  return app.admin_append(v_actor, p_type, null, p_detail, v_reason);
end
$$;

-- Grants: the doors for signed-in people; the helpers for nobody (they run as the owner).
revoke all on function app.admin_caller(), app.admin_reason(text), app.admin_name(uuid),
                       app.admin_append(uuid, text, uuid, jsonb, text), app.admin_keep_one(),
                       app.admin_person(uuid), app.admin_subject(uuid),
                       app.admin_people(), app.admin_set_role(uuid, app.user_role, boolean, text),
                       app.admin_set_ramp_link(uuid, text, text), app.admin_set_access(uuid, boolean, text),
                       app.admin_log(integer), app.admin_record_demo(text, jsonb, text)
  from public, anon, authenticated, service_role;
grant execute on function app.admin_caller(), app.admin_people(), app.admin_set_role(uuid, app.user_role, boolean, text),
                          app.admin_set_ramp_link(uuid, text, text), app.admin_set_access(uuid, boolean, text),
                          app.admin_log(integer), app.admin_record_demo(text, jsonb, text)
  to authenticated;
