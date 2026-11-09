-- 015: demo accounts act only on demo datasets (spec 0034).
begin;
\i supabase/tests/_helpers.sql

-- A case waiting for review, its hold confirmed. p_changer null is a change with no known author.
create function pg_temp.case_needing_review(p_key text, p_changer uuid) returns uuid
language plpgsql as $$
declare
  c uuid;
begin
  perform pg_temp.sign_out();
  c := (app.apply_case_event(null, 'change_detected', pg_temp.sys(), p_key || ':detected',
          jsonb_build_object('change_event_id', pg_temp.new_change(p_key || '0001', p_changer))) ->> 'case_id')::uuid;
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), p_key || ':hold:confirmed', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'no_provable_number', pg_temp.sys(), p_key || ':no_number', '{}');
  return c;
end
$$;

create function pg_temp.body(p_case uuid) returns jsonb
language sql as $$ select jsonb_build_object('reason', 'Checked.', 'expected_version', version) from app.cases where id = p_case $$;

-- The dataset's kind, set as the owner past the trigger that otherwise refuses it.
create function pg_temp.set_kind(p_dataset uuid, p_kind app.dataset_kind) returns void
language plpgsql as $$
begin
  alter table app.datasets disable trigger datasets_kind_is_fixed;
  update app.datasets set kind = p_kind where id = p_dataset;
  alter table app.datasets enable trigger datasets_kind_is_fixed;
end
$$;

do $$
declare
  f     pg_temp.fx%rowtype;
  demo  uuid := gen_random_uuid();
  c     uuid;
  u     uuid;
  n     integer;
  ev    text;
begin
  f := pg_temp.setup_fixture();                       -- a 'dev' dataset
  insert into app.user_roles (user_id, role) values (demo, 'approver');
  insert into app.demo_accounts (user_id) values (demo);

  -- Nothing can turn a dataset into a demo one, so the rule below can trust the kind.
  perform pg_temp.expect_error(format($q$ update app.datasets set kind = 'demo' where id = %L $q$, f.dataset), '42501', '%kind can''t change%');

  -- Off demo data, the demo approver can neither clear nor block a review; nothing moves.
  c := pg_temp.case_needing_review('k1', f.clerk);
  perform pg_temp.sign_in(demo);
  foreach ev in array array['review_cleared', 'review_blocked'] loop
    perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, %L, pg_temp.person(%L), %L, %L) $q$,
              c, ev, demo, 'd:' || ev, pg_temp.body(c)), '42501', '%demo account%');
  end loop;
  assert pg_temp.state_of(c) = 'needs_review', 'the demo account moved nothing';

  -- A real approver clears it; the demo approver can't approve or reject the release.
  perform pg_temp.sign_in(f.approver1);
  perform app.apply_case_event(c, 'review_cleared', pg_temp.person(f.approver1), 'r1', pg_temp.body(c));
  perform pg_temp.sign_in(demo);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'demo') $q$, c, (select version from app.cases where id = c)), '42501', '%demo account%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'reject', 'demo') $q$, c, (select version from app.cases where id = c)), '42501', '%demo account%');
  assert pg_temp.state_of(c) = 'awaiting_release', 'still awaiting release';
  assert not exists (select 1 from app.releases where case_id = c), 'no approval was recorded';
  assert (select status::text = 'confirmed' and released_at is null from app.holds where case_id = c), 'the hold is still on';

  -- With no known author a first approval only records itself (ADR-10): refused all the same.
  perform pg_temp.sign_in(f.approver1);
  perform app.apply_case_event(c, 'release_rejected', pg_temp.person(f.approver1), 'rej1', pg_temp.body(c));  -- frees the vendor
  u := pg_temp.case_needing_review('k2', null);  -- the same vendor, so the blocked case reopens with no known author
  assert (select has_unknown_changer from app.cases where id = u), 'two approvers needed';
  perform pg_temp.sign_in(f.approver1);
  perform app.apply_case_event(u, 'review_cleared', pg_temp.person(f.approver1), 'r2', pg_temp.body(u));
  perform pg_temp.sign_in(demo);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'demo') $q$, u, (select version from app.cases where id = u)), '42501', '%demo account%');
  assert not exists (select 1 from app.releases where case_id = u and actor_id = demo), 'no co-approval was recorded';

  -- Demo accounts see only demo datasets and their cases, and can't read or write the marker table.
  set local role authenticated;
  perform pg_temp.sign_in(demo);
  select count(*) into n from app.datasets where id = f.dataset;  assert n = 0, 'a demo account sees no dev dataset';
  select count(*) into n from app.cases where id = c;             assert n = 0, 'nor its cases';
  perform pg_temp.sign_in(f.approver1);
  select count(*) into n from app.datasets where id = f.dataset;  assert n = 1, 'an approver still sees it';
  select count(*) into n from app.cases where id = c;             assert n = 1, 'and its cases';
  perform pg_temp.expect_error($q$ select count(*) from app.demo_accounts $q$, '42501');
  perform pg_temp.expect_error($q$ insert into app.demo_accounts (user_id) values (gen_random_uuid()) $q$, '42501');
  reset role;

  -- On a demo dataset the same account reviews and releases.
  perform pg_temp.set_kind(f.dataset, 'demo');
  perform pg_temp.sign_in(f.approver2);  -- not approver1, who cleared the review (R5)
  perform app.release_case(u, (select version from app.cases where id = u), 'approve', 'First of two approvers.');
  perform pg_temp.sign_in(demo);
  assert app.release_case(u, (select version from app.cases where id = u), 'approve', 'Second approver, on demo data.') ->> 'to_state' = 'releasing',
    'a demo approver releases on demo data';
end
$$;

rollback;
