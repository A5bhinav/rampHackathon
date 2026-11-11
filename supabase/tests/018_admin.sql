-- 018: the admin role (spec 0095). An admin holds every role's powers, manages people through
-- the app.admin_* doors, and every admin action lands on the organisation chain (R6). R5 holds
-- for an admin exactly as for anyone else.
begin;
\i supabase/tests/_helpers.sql

-- A stand-in auth.users with the columns admin_people() reads (plain Postgres has none).
create table auth.users (id uuid primary key, email text, last_sign_in_at timestamptz,
                         raw_user_meta_data jsonb, is_anonymous boolean not null default false);

-- A case waiting for review, on its own vendor (one vendor allows one open case); p_changer null
-- is a change with no known author.
create function pg_temp.case_needing_review(p_key text, p_changer uuid) returns uuid
language plpgsql as $$
declare
  f        pg_temp.fx%rowtype;
  v_vendor uuid;
  v_old    uuid;
  v_new    uuid;
  v_change uuid;
  c        uuid;
begin
  select * into f from pg_temp.fx limit 1;
  insert into app.vendors (dataset_id, ramp_vendor_id, legal_name) values (f.dataset, 'ven_' || p_key, 'Vendor ' || p_key)
    returning id into v_vendor;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint, first_seen_at)
    values (f.dataset, v_vendor, '021000021', '1111', pg_temp.fingerprint('021000021', p_key || 'old'), now() - interval '400 days')
    returning id into v_old;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint)
    values (f.dataset, v_vendor, '026009593', '2222', pg_temp.fingerprint('026009593', p_key || 'new'))
    returning id into v_new;
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id, changed_by)
    values (f.dataset, v_vendor, v_old, v_new, 'poll', 'evt_' || p_key, p_changer)
    returning id into v_change;
  perform pg_temp.sign_out();
  c := (app.apply_case_event(null, 'change_detected', pg_temp.sys(), p_key || ':detected',
          jsonb_build_object('change_event_id', v_change)) ->> 'case_id')::uuid;
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), p_key || ':hold:confirmed', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'no_provable_number', pg_temp.sys(), p_key || ':no_number', '{}');
  return c;
end
$$;

create function pg_temp.body(p_case uuid) returns jsonb
language sql as $$ select jsonb_build_object('reason', 'Checked.', 'expected_version', version) from app.cases where id = p_case $$;

create function pg_temp.version_of(p_case uuid) returns integer
language sql as $$ select version from app.cases where id = p_case $$;

create function pg_temp.org_events() returns bigint
language sql as $$ select count(*) from audit.evidence_events e join app.org_settings o on o.chain_id = e.chain_id $$;

create function pg_temp.roles_of(p_user uuid) returns text[]
language sql as $$ select coalesce(array_agg(role::text order by role::text), '{}') from app.user_roles where user_id = p_user $$;

-- A person: an auth account and a profile, with the given roles.
create function pg_temp.person_with(p_name text, p_roles app.user_role[]) returns uuid
language plpgsql as $$
declare
  u uuid := gen_random_uuid();
  r app.user_role;
begin
  insert into auth.users (id, email, last_sign_in_at) values (u, lower(replace(p_name, ' ', '.')) || '@example.test', now());
  insert into app.user_profiles (user_id, display_name) values (u, p_name);
  foreach r in array p_roles loop
    insert into app.user_roles (user_id, role) values (u, r);
  end loop;
  return u;
end
$$;

do $$
declare
  f       pg_temp.fx%rowtype;
  admin1  uuid;
  admin2  uuid;
  clerk2  uuid;
  auditor uuid;
  demo    uuid;
  c       uuid;
  n       bigint;
  r       jsonb;
  who     uuid;
  q       text;
  r_chain uuid;
  demo_ds uuid;
  v_vendor uuid;
  v_acct  uuid;
  v_ev    uuid;
begin
  f := pg_temp.setup_fixture();
  insert into audit.chains (label) values ('demo-018') returning id into r_chain;
  insert into app.datasets (kind, label, chain_id) values ('demo', 'demo-018', r_chain) returning id into demo_ds;
  admin1  := pg_temp.person_with('Ada Admin', array['admin']::app.user_role[]);
  clerk2  := pg_temp.person_with('Sam Clerk', array['clerk']::app.user_role[]);
  auditor := pg_temp.person_with('Avery Auditor', array['auditor']::app.user_role[]);
  demo    := pg_temp.person_with('Demo Approver', array['approver']::app.user_role[]);
  insert into app.demo_accounts (user_id) values (demo);
  insert into app.user_profiles (user_id, display_name) values (f.clerk, 'Fixture Clerk'), (f.approver1, 'Fixture Approver'),
                                                             (f.approver2, 'Second Approver');
  insert into auth.users (id, email) values (f.clerk, 'clerk@example.test'), (f.approver1, 'approver1@example.test');

  -- The organisation chain exists from the migration, empty.
  assert (select chain_id is not null from app.org_settings), 'the org chain is set';
  assert (select count(*) from app.org_settings) = 1, 'one org_settings row';
  assert pg_temp.org_events() = 0, 'the org chain starts empty';

  -- D1: admin counts as every role.
  assert app.has_role(admin1, 'approver') and app.has_role(admin1, 'clerk') and app.has_role(admin1, 'auditor'),
    'an admin holds every role';
  assert not app.has_role(f.approver1, 'admin'), 'an approver is not an admin';

  -- A1: an admin reviews and releases a change someone else entered.
  c := pg_temp.case_needing_review('a1', f.clerk);
  perform pg_temp.sign_in(admin1);
  assert app.apply_case_event(c, 'review_cleared', pg_temp.person(admin1), c || ':clear', pg_temp.body(c)) ->> 'to_state'
         = 'awaiting_release', 'an admin clears a review';
  -- A2: the admin who cleared it can't also release it; a different approver can.
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'mine') $q$, c, pg_temp.version_of(c)),
            '23514', '%reviewer who cleared%');
  perform pg_temp.sign_in(f.approver1);
  perform app.release_case(c, pg_temp.version_of(c), 'approve', 'Checked the call.');
  assert pg_temp.state_of(c) in ('releasing', 'released'), 'released by a different person';

  -- A1: an admin releases a case another person cleared.
  c := pg_temp.case_needing_review('a1b', f.clerk);
  perform pg_temp.sign_in(f.approver1);
  perform app.apply_case_event(c, 'review_cleared', pg_temp.person(f.approver1), c || ':clear', pg_temp.body(c));
  perform pg_temp.sign_in(admin1);
  perform app.release_case(c, pg_temp.version_of(c), 'approve', 'Checked the call.');
  assert pg_temp.state_of(c) in ('releasing', 'released'), 'an admin releases another person''s change';

  -- A2: R5 holds for an admin. A change the admin entered can't be cleared, released or rejected by
  -- them. (Blocking is the safe direction, and spec 0028 lets the changer block, admin or not.)
  c := pg_temp.case_needing_review('a2', admin1);
  perform pg_temp.sign_in(admin1);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'review_cleared', pg_temp.person(%L), 'x1', %L) $q$,
            c, admin1, pg_temp.body(c)), '23514', '%guard failed%');
  perform pg_temp.sign_in(f.approver1);
  perform app.apply_case_event(c, 'review_cleared', pg_temp.person(f.approver1), c || ':clear', pg_temp.body(c));
  perform pg_temp.sign_in(admin1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'mine') $q$, c, pg_temp.version_of(c)),
            '23514', '%entered the change%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'reject', 'mine') $q$, c, pg_temp.version_of(c)),
            '23514', '%entered the change%');
  assert pg_temp.state_of(c) = 'awaiting_release', 'the admin moved nothing on their own change';

  -- A2: an unknown changer still needs two different people: an admin's approval is the first, not the release.
  c := pg_temp.case_needing_review('a2u', null);
  perform pg_temp.sign_in(f.approver1);
  perform app.apply_case_event(c, 'review_cleared', pg_temp.person(f.approver1), c || ':clear', pg_temp.body(c));
  perform pg_temp.sign_in(admin1);
  assert app.release_case(c, pg_temp.version_of(c), 'approve', 'First of two.') ->> 'status' = 'awaiting_second_approver',
    'an admin is the first of two';
  assert pg_temp.state_of(c) = 'awaiting_release', 'one approval doesn''t release an unknown changer''s case';

  -- A3: every door refuses a non-admin, nobody, a demo account, and a turned-off admin. Nothing is logged.
  n := pg_temp.org_events();
  foreach q in array array[
      format($q$ select app.admin_people() $q$),
      format($q$ select app.admin_set_role(%L, 'approver', true, 'r') $q$, clerk2),
      format($q$ select app.admin_set_ramp_link(%L, 'ramp_x', 'r') $q$, clerk2),
      format($q$ select app.admin_set_access(%L, false, 'r') $q$, clerk2),
      format($q$ select app.admin_log(10) $q$),
      format($q$ select app.admin_record_demo('demo_chaos', '{"dataset_id": "%s", "enabled": true}', 'r') $q$, demo_ds)] loop
    foreach who in array array[clerk2, auditor, f.approver1, demo] loop
      perform pg_temp.sign_in(who);
      perform pg_temp.expect_error(q, '42501');
    end loop;
    perform pg_temp.sign_out();
    perform pg_temp.expect_error(q, '42501', '%sign in%');
  end loop;
  assert pg_temp.org_events() = n, 'a refused door logs nothing';

  -- A4: grant and revoke, with a reason; the refusals change and log nothing.
  perform pg_temp.sign_in(admin1);
  r := app.admin_set_role(clerk2, 'approver', true, 'Covers releases this week.');
  assert pg_temp.roles_of(clerk2) = array['approver', 'clerk'], 'approver granted';
  assert r ->> 'id' = clerk2::text and r -> 'roles' ? 'approver', 'returns the person';
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'approver', true, 'again') $q$, clerk2), '23514', '%already%');
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'auditor', false, 'none') $q$, clerk2), '23514', '%doesn''t have%');
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'auditor', true, '  ') $q$, clerk2), '23514', '%reason%');
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'auditor', true, 'who') $q$, gen_random_uuid()), 'P0002');
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'admin', true, 'judge') $q$, demo), '23514', '%demo account%');
  perform app.admin_set_role(clerk2, 'approver', false, 'Back from leave.');
  assert pg_temp.roles_of(clerk2) = array['clerk'], 'approver revoked';
  assert pg_temp.org_events() = n + 2, 'two grants, two events';
  perform app.admin_set_role(auditor, 'clerk', true, 'Also enters bills.');
  perform app.admin_set_role(auditor, 'clerk', false, 'Not any more.');
  perform app.admin_set_role(clerk2, 'auditor', true, 'Year-end.');
  perform app.admin_set_role(clerk2, 'auditor', false, 'Year-end done.');
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'clerk', true, %L) $q$, clerk2, repeat('x', 2001)), '23514', '%2000%');
  assert pg_temp.roles_of(clerk2) = array['clerk'] and pg_temp.roles_of(auditor) = array['auditor'], 'every role both ways';
  n := pg_temp.org_events();

  -- A4, A5: the last admin can't be removed; a second admin can remove the first, but not then themselves.
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'admin', false, 'leaving') $q$, admin1), '23514', '%last admin%');
  admin2 := pg_temp.person_with('Bo Admin', array['clerk']::app.user_role[]);
  perform app.admin_set_role(admin2, 'admin', true, 'Second admin.');
  perform pg_temp.sign_in(admin2);
  perform app.admin_set_role(admin1, 'admin', false, 'Ada moves teams.');
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'admin', false, 'me too') $q$, admin2), '23514', '%last admin%');
  assert (select count(*) from app.user_roles where role = 'admin') = 1, 'exactly one admin left';
  perform app.admin_set_role(admin1, 'admin', true, 'Ada is back.');

  -- A6: Ramp links. Not your own; one person per Ramp user; null clears.
  perform pg_temp.sign_in(admin1);
  perform pg_temp.expect_error(format($q$ select app.admin_set_ramp_link(%L, 'ramp_self', 'me') $q$, admin1), '23514', '%your own%');
  perform app.admin_set_ramp_link(clerk2, 'ramp_sam', 'Sam enters changes in Ramp.');
  assert (select ramp_user_id from app.user_profiles where user_id = clerk2) = 'ramp_sam', 'linked';
  perform pg_temp.expect_error(format($q$ select app.admin_set_ramp_link(%L, 'ramp_sam', 'dup') $q$, auditor), '23514', '%already linked%');
  perform app.admin_set_ramp_link(clerk2, null, 'Sam no longer uses Ramp.');
  assert (select ramp_user_id is null from app.user_profiles where user_id = clerk2), 'cleared';

  -- A7: access off and on keeps roles; not yourself; never the last enabled admin.
  perform app.admin_set_access(clerk2, false, 'Left on Nov 10.');
  assert (select disabled_at is not null and disabled_by = admin1 from app.user_profiles where user_id = clerk2), 'turned off';
  assert pg_temp.roles_of(clerk2) = array['clerk'], 'roles kept';
  perform pg_temp.expect_error(format($q$ select app.admin_set_access(%L, false, 'again') $q$, clerk2), '23514', '%already%');
  perform app.admin_set_access(clerk2, true, 'Rehired.');
  assert (select disabled_at is null from app.user_profiles where user_id = clerk2), 'back on';
  perform pg_temp.expect_error(format($q$ select app.admin_set_access(%L, false, 'me') $q$, admin1), '23514', '%your own%');
  perform app.admin_set_access(admin2, false, 'Bo is away.');
  perform pg_temp.sign_in(admin2);
  foreach q in array array[
      $q$ select app.admin_people() $q$, $q$ select app.admin_log(5) $q$, $q$ select app.admin_caller() $q$,
      format($q$ select app.admin_set_role(%L, 'auditor', true, 'r') $q$, clerk2),
      format($q$ select app.admin_set_ramp_link(%L, 'ramp_z', 'r') $q$, clerk2),
      format($q$ select app.admin_set_access(%L, false, 'r') $q$, clerk2),
      format($q$ select app.admin_record_demo('demo_tamper', '{"dataset_id": "%s"}', 'r') $q$, demo_ds)] loop
    perform pg_temp.expect_error(q, '42501', '%turned off%');
  end loop;
  perform pg_temp.sign_in(admin1);
  perform app.admin_set_role(admin2, 'admin', false, 'Bo is away for good.');
  perform pg_temp.expect_error(format($q$ select app.admin_set_role(%L, 'admin', false, 'x') $q$, admin1), '23514', '%last admin%');

  -- A8: every action above is on the org chain with actor, subject, detail and reason; the chain verifies.
  assert (select bool_and(e.actor_kind = 'person' and e.dataset_id is null and e.case_id is null)
            from audit.evidence_events e join app.org_settings o on o.chain_id = e.chain_id), 'person actor, no dataset or case';
  assert (select body_jcs::jsonb from audit.evidence_events e join app.org_settings o on o.chain_id = e.chain_id
           where e.type = 'role_granted' order by seq limit 1)
         @> jsonb_build_object('type', 'role_granted', 'subject_user_id', clerk2, 'detail', jsonb_build_object('role', 'approver'),
                               'reason', 'Covers releases this week.', 'actor', pg_temp.person(admin1)), 'the first grant, in full';
  assert (select array_agg(type order by seq) from audit.evidence_events e join app.org_settings o on o.chain_id = e.chain_id)
         = array['role_granted', 'role_revoked', 'role_granted', 'role_revoked', 'role_granted', 'role_revoked',
                 'role_granted', 'role_revoked', 'role_granted', 'ramp_link_set', 'ramp_link_set',
                 'access_disabled', 'access_enabled', 'access_disabled', 'role_revoked'],
    'one event per successful action, in order';
  assert (select ok from audit.verify_chain((select chain_id from app.org_settings))), 'the org chain verifies';

  -- A9: admin_people lists everyone, for admins only; admin_log reads the chain newest first.
  r := app.admin_people();
  assert jsonb_array_length(r) = (select count(*) from app.user_profiles), 'everyone with a profile';
  assert (select p ->> 'email' from jsonb_array_elements(r) p where p ->> 'id' = clerk2::text) = 'sam.clerk@example.test', 'email';
  assert (select (p ->> 'demo_only')::boolean from jsonb_array_elements(r) p where p ->> 'id' = demo::text), 'demo flag';
  assert (select not (p ->> 'access_enabled')::boolean from jsonb_array_elements(r) p where p ->> 'id' = admin2::text), 'access';
  r := app.admin_log(3);
  assert (r -> 'chain' ->> 'ok')::boolean and jsonb_array_length(r -> 'events') = 3, 'three events and a verified chain';
  assert (r -> 'events' -> 0 ->> 'type') = 'role_revoked' and (r -> 'events' -> 0 -> 'actor' ->> 'display_name') = 'Ada Admin',
    'newest first, with names';

  -- D5: the admin who set a person's Ramp link is a changer of that person's changes (R5), so
  -- linking someone else to your own Ramp identity doesn't let you release your change.
  perform pg_temp.sign_in(admin1);
  perform app.admin_set_ramp_link(clerk2, 'ramp_sam2', 'Sam is back on Ramp.');
  c := pg_temp.case_needing_review('d5', clerk2);
  assert (select admin1 = any (changer_ids) and clerk2 = any (changer_ids) from app.cases where id = c), 'the linker is a changer';
  perform pg_temp.sign_in(f.approver1);
  perform app.apply_case_event(c, 'review_cleared', pg_temp.person(f.approver1), c || ':clear', pg_temp.body(c));
  perform pg_temp.sign_in(admin1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'linked') $q$, c, pg_temp.version_of(c)),
            '23514', '%entered the change%');


  -- D5 on an unfinished case: a new change made through a link admin1 set adds admin1 as a changer.
  c := pg_temp.case_needing_review('d5b', f.approver1);
  perform pg_temp.sign_in(admin1);
  perform app.admin_set_ramp_link(f.clerk, 'ramp_fixture_clerk', 'The fixture clerk uses Ramp.');
  select vendor_id into v_vendor from app.cases where id = c;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint)
    values (f.dataset, v_vendor, '026009593', '3333', pg_temp.fingerprint('026009593', 'd5b-second')) returning id into v_acct;
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id, changed_by,
                                      changed_by_ramp_user_id)
    values (f.dataset, v_vendor, (select new_account_id from app.bank_change_events where id = (select change_event_id from app.cases where id = c)),
            v_acct, 'poll', 'evt_d5b_2', f.clerk, 'ramp_fixture_clerk') returning id into v_ev;
  perform pg_temp.sign_out();
  perform app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'd5b:second', jsonb_build_object('change_event_id', v_ev));
  assert (select admin1 = any (changer_ids) and f.clerk = any (changer_ids) and f.approver1 = any (changer_ids) from app.cases where id = c),
    'the new change''s changer and linker join the earlier changer';
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), 'd5b:hold2', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'no_provable_number', pg_temp.sys(), 'd5b:no_number2', '{}');
  assert pg_temp.state_of(c) = 'needs_review', 'back to review';
  perform pg_temp.sign_in(admin1);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'review_cleared', pg_temp.person(%L), 'lk2', %L) $q$,
            c, admin1, pg_temp.body(c)), '23514', '%guard failed%');

  -- D5: a change whose author no longer holds the Ramp user it came from has no known author.
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id, changed_by,
                                      changed_by_ramp_user_id)
    values (f.dataset, v_vendor, v_acct, v_acct, 'poll', 'evt_moved', f.clerk, 'ramp_someone_else') returning id into v_ev;
  assert (select changed_by is null and changed_by_linker is null from app.bank_change_events where id = v_ev), 'moved link: unknown author';

  -- D7: a person whose access is off holds no role in the database, not only in the core.
  perform app.admin_set_access(f.approver2, false, 'On leave.');
  assert not app.has_role(f.approver2, 'approver'), 'access off: no role';
  perform pg_temp.sign_in(f.approver2);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'off') $q$, c, pg_temp.version_of(c)),
            '42501', '%approver role required%');
  perform pg_temp.sign_in(admin1);

  -- D9: the core records a demo action as the admin, before it runs the command.
  perform app.admin_record_demo('demo_chaos', jsonb_build_object('dataset_id', demo_ds, 'enabled', true), 'Show a failed hold.');
  perform pg_temp.expect_error($q$ select app.admin_record_demo('role_granted', '{}', 'x') $q$, '23514', '%demo action%');
  foreach q in array array[
      format($q$ select app.admin_record_demo('demo_chaos', '{"dataset_id": "%s", "enabled": true}', 'not the demo') $q$, f.dataset),
      format($q$ select app.admin_record_demo('demo_chaos', '{"dataset_id": "%s", "enabled": true, "x": 1}', 'extra key') $q$, demo_ds),
      format($q$ select app.admin_record_demo('demo_tamper', '{"dataset_id": "%s", "seq": 3}', 'extra key') $q$, demo_ds),
      $q$ select app.admin_record_demo('demo_reset', '{"linked_clerk_user_id": "00000000-0000-4000-8000-000000000000"}', 'nobody') $q$,
      $q$ select app.admin_record_demo('demo_reset', '[]', 'not an object') $q$] loop
    perform pg_temp.expect_error(q, '23514', '%valid%');
  end loop;
  assert (select type from audit.evidence_events e join app.org_settings o on o.chain_id = e.chain_id order by seq desc limit 1)
         = 'demo_chaos', 'demo action logged';
end
$$;

-- Signed-in people can't write the new tables or the org chain directly.
set local role authenticated;
select pg_temp.sign_in((select user_id from app.user_roles where role = 'admin' limit 1));
select pg_temp.expect_error($q$ update app.org_settings set chain_id = gen_random_uuid() $q$, '42501');
select pg_temp.expect_error($q$ insert into app.user_roles (user_id, role) values (gen_random_uuid(), 'admin') $q$, '42501');
select pg_temp.expect_error($q$ update app.user_profiles set disabled_at = null $q$, '42501');
reset role;

-- D5, D7: the core's server role can't grant admin, turn access on or off, or quietly clear a linker.
set local role service_role;
select pg_temp.expect_error($q$ insert into app.user_roles (user_id, role) values (gen_random_uuid(), 'admin') $q$, '42501', '%server role%');
select pg_temp.expect_error($q$ update app.user_profiles set disabled_at = now() $q$, '42501', '%server role%');
select pg_temp.expect_error($q$ update app.user_profiles set ramp_linked_by = null where ramp_linked_by is not null $q$, '42501', '%server role%');
select pg_temp.expect_error($q$ update app.bank_change_events set changed_by_linker = null where changed_by_linker is not null $q$, '42501');
reset role;

-- A11: only the doors are executable, and only by signed-in people; the helpers by nobody.
do $$
declare
  door   text;
  helper text;
begin
  foreach door in array array['app.admin_caller()', 'app.admin_people()', 'app.admin_set_role(uuid, app.user_role, boolean, text)',
                              'app.admin_set_ramp_link(uuid, text, text)', 'app.admin_set_access(uuid, boolean, text)',
                              'app.admin_log(integer)', 'app.admin_record_demo(text, jsonb, text)'] loop
    assert has_function_privilege('authenticated', door, 'execute'), door || ' for signed-in people';
    assert not has_function_privilege('anon', door, 'execute'), door || ' not for anon';
    assert not has_function_privilege('service_role', door, 'execute'), door || ' not for the server key (ADR-9)';
  end loop;
  foreach helper in array array['app.admin_append(uuid, text, uuid, jsonb, text)', 'app.admin_keep_one()', 'app.admin_person(uuid)',
                                'app.admin_subject(uuid)', 'app.admin_name(uuid)', 'app.admin_reason(text)',
                                'app.record_ramp_linker()', 'app.linker_is_a_changer()'] loop
    assert not has_function_privilege('authenticated', helper, 'execute')
       and not has_function_privilege('service_role', helper, 'execute')
       and not has_function_privilege('anon', helper, 'execute'), helper || ' for nobody';
  end loop;
end
$$;

rollback;
