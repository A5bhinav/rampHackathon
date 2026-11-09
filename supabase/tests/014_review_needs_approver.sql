-- 014: only an approver clears or blocks a review (spec 0028). Anyone can sign up (spec 0027),
-- so a review that needed no role could be made by anyone.
begin;
\i supabase/tests/_helpers.sql

create function pg_temp.case_needing_review(f pg_temp.fx, p_key text) returns uuid
language plpgsql as $$
declare
  c uuid;
begin
  c := (app.apply_case_event(null, 'change_detected', pg_temp.sys(), p_key || ':detected',
          jsonb_build_object('change_event_id', pg_temp.new_change('000008888', f.clerk))) ->> 'case_id')::uuid;
  -- A confirmed hold, then no number to call: the hold is really on while it waits for review.
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), p_key || ':hold:confirmed', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'no_provable_number', pg_temp.sys(), p_key || ':no_number', '{}');
  assert pg_temp.state_of(c) = 'needs_review', 'the case needs review';
  return c;
end
$$;

do $$
declare
  f        pg_temp.fx%rowtype;
  c        uuid;
  v        integer;
  body     text;
begin
  f := pg_temp.setup_fixture();
  c := pg_temp.case_needing_review(f, 'k1');
  v := (select version from app.cases where id = c);
  body := format('{"reason": "I called them and they confirmed.", "expected_version": %s}', v);

  -- A second clerk (not the changer), an auditor, and an account with no role: all refused,
  -- for clearing and for blocking. The case stays put and the hold stays on.
  declare
    clerk2  uuid := gen_random_uuid();
    auditor uuid := gen_random_uuid();
    nobody  uuid := gen_random_uuid();
    who     uuid;
    ev      text;
  begin
    insert into app.user_roles (user_id, role) values (clerk2, 'clerk'), (auditor, 'auditor');
    foreach who in array array[clerk2, auditor, nobody] loop
      foreach ev in array array['review_cleared', 'review_blocked'] loop
        perform pg_temp.sign_in(who);
        perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, %L, pg_temp.person(%L), %L, %L) $q$,
                  c, ev, who, c || ':' || ev || ':' || who, body), '23514', '%guard failed%');
      end loop;
    end loop;
  end;
  assert pg_temp.state_of(c) = 'needs_review', 'nobody without the role moved it';
  assert (select status::text = 'confirmed' and released_at is null from app.holds where case_id = c), 'the hold is still on';

  -- The changer is refused even as an approver (R5). An approver needs a reason, and with one blocks.
  insert into app.user_roles (user_id, role) values (f.clerk, 'approver') on conflict do nothing;
  -- (An approver clearing is in 002_case_machine.sql; one vendor allows one open case.)
  perform pg_temp.sign_in(f.clerk);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'review_cleared', pg_temp.person(%L), 'x', %L) $q$,
            c, f.clerk, body), '23514', '%guard failed%');
  perform pg_temp.sign_in(f.approver2);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'review_blocked', pg_temp.person(%L), 'y', %L) $q$,
            c, f.approver2, format('{"reason": " ", "expected_version": %s}', v)), '23514', '%guard failed%');
  assert app.apply_case_event(c, 'review_blocked', pg_temp.person(f.approver2), c || ':block',
           format('{"reason": "The vendor denied the change.", "expected_version": %s}', v)::jsonb) ->> 'to_state' = 'blocked',
    'an approver blocks';

  -- The old guard names are gone: a transition still naming one would refuse, never pass.
  perform pg_temp.expect_error($q$ select app.eval_guard('reason_given', null::app.cases, null, '{"reason": "x"}') $q$, null, '%unknown guard%');
  assert not exists (select 1 from app.case_transitions where guard_key in ('reason_given', 'reason_given_not_changer')),
    'no transition names an old guard';
end
$$;

rollback;
