-- 015: demo accounts act only on demo datasets (spec 0034).
begin;
\i supabase/tests/_helpers.sql

create function pg_temp.case_needing_review(f pg_temp.fx, p_key text) returns uuid
language plpgsql as $$
declare
  c uuid;
begin
  c := (app.apply_case_event(null, 'change_detected', pg_temp.sys(), p_key || ':detected',
          jsonb_build_object('change_event_id', pg_temp.new_change(p_key || '0001', f.clerk))) ->> 'case_id')::uuid;
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), p_key || ':hold:confirmed', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'no_provable_number', pg_temp.sys(), p_key || ':no_number', '{}');
  return c;
end
$$;

do $$
declare
  f     pg_temp.fx%rowtype;
  demo  uuid := gen_random_uuid();
  c     uuid;
  v     integer;
  n     integer;
begin
  f := pg_temp.setup_fixture();                       -- a 'dev' dataset
  insert into app.user_roles (user_id, role) values (demo, 'approver');
  insert into app.demo_accounts (user_id) values (demo);
  c := pg_temp.case_needing_review(f, 'k1');
  v := (select version from app.cases where id = c);

  -- Off demo data, the demo approver can't review: the case and the hold stay put.
  perform pg_temp.sign_in(demo);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'review_blocked', pg_temp.person(%L), 'd1', %L) $q$,
            c, demo, format('{"reason": "demo", "expected_version": %s}', v)), '42501', '%demo account%');
  assert pg_temp.state_of(c) = 'needs_review', 'the demo account moved nothing';

  -- A real approver clears it; the demo approver still can't approve or reject the release.
  perform pg_temp.sign_in(f.approver1);
  perform app.apply_case_event(c, 'review_cleared', pg_temp.person(f.approver1), 'r1',
            format('{"reason": "Checked by phone.", "expected_version": %s}', v)::jsonb);
  v := (select version from app.cases where id = c);
  perform pg_temp.sign_in(demo);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'demo') $q$, c, v), '42501', '%demo account%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'reject', 'demo') $q$, c, v), '42501', '%demo account%');
  assert pg_temp.state_of(c) = 'awaiting_release', 'still awaiting release';
  assert not exists (select 1 from app.releases where case_id = c), 'no approval was recorded';

  -- The system is untouched by the rule: no one is signed in for its events.
  perform pg_temp.sign_out();
  assert app.apply_case_event(c, 'change_detected', pg_temp.sys(), 'k1:again',
           jsonb_build_object('change_event_id', pg_temp.new_change('k20002', f.clerk))) ->> 'to_state' = 'held',
    'a system event still applies';

  -- Demo accounts see only demo datasets and their cases; everyone else sees everything.
  set local role authenticated;
  perform pg_temp.sign_in(demo);
  select count(*) into n from app.datasets where id = f.dataset;  assert n = 0, 'a demo account sees no dev dataset';
  select count(*) into n from app.cases where id = c;             assert n = 0, 'nor its cases';
  perform pg_temp.sign_in(f.approver1);
  select count(*) into n from app.datasets where id = f.dataset;  assert n = 1, 'an approver still sees it';
  select count(*) into n from app.cases where id = c;             assert n = 1, 'and its cases';
  perform pg_temp.expect_error($q$ insert into app.demo_accounts (user_id) values (gen_random_uuid()) $q$, '42501');
  reset role;

  -- On a demo dataset the same account can act.
  update app.datasets set kind = 'demo' where id = f.dataset;
  perform pg_temp.sign_out();
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), 'k1:hold2', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'no_provable_number', pg_temp.sys(), 'k1:none2', '{}');
  v := (select version from app.cases where id = c);
  perform pg_temp.sign_in(demo);
  assert app.apply_case_event(c, 'review_blocked', pg_temp.person(demo), 'd2',
           format('{"reason": "The vendor denied it.", "expected_version": %s}', v)::jsonb) ->> 'to_state' = 'blocked',
    'a demo approver acts on demo data';
end
$$;

rollback;
