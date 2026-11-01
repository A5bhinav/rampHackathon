-- 002: one case from detection to release through the single door, with retries, idempotency,
-- a second change that re-holds, exhaustion into review, and the two-approver path.
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f        pg_temp.fx%rowtype;
  r        jsonb;
  c1       uuid;
  c2       uuid;
  v_change uuid;
  v_before bigint;
begin
  f := pg_temp.setup_fixture();

  -- Detect: a new change opens a case, queues the hold and the deadline timer.
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_1:detected',
         jsonb_build_object('change_event_id', f.change1, 'policy_version', 'v0'));
  assert r ->> 'status' = 'applied' and r ->> 'to_state' = 'open' and (r ->> 'version')::int = 1, r::text;
  c1 := (r ->> 'case_id')::uuid;
  assert (select entered_by = f.clerk and changer_ids = array[f.clerk] and not has_unknown_changer from app.cases where id = c1), 'changer recorded';
  assert exists (select 1 from app.outbox where case_id = c1 and command = 'place_hold' and idempotency_key = c1 || ':hold:v1'), 'hold queued';
  assert exists (select 1 from app.timers where case_id = c1 and kind = 'deadline' and status = 'scheduled'), 'deadline timer';
  assert (select deadline_at > now() + interval '2 days' from app.cases where id = c1), 'deadline in business days';

  -- A second detection of the same change is a no-op; a case cannot be opened twice for one vendor.
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_1:detected', jsonb_build_object('change_event_id', f.change1));
  assert r ->> 'status' = 'already_applied', 'idempotent detection';

  -- Hold: the guard wants the read-back.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'hold_confirmed', pg_temp.sys(), 'x1', '{"readback_ok": false}') $q$, c1), '23514', '%guard failed%');
  r := app.apply_case_event(c1, 'hold_confirmed', pg_temp.sys(), c1 || ':hold:v1:confirmed',
         jsonb_build_object('readback_ok', true, 'ramp_ref', 'hold_123',
                            'original_schedule', jsonb_build_array(jsonb_build_object('bill_id', 'bill_1', 'payment_date', '2026-11-05'))));
  assert r ->> 'to_state' = 'held', r::text;
  assert (select status = 'confirmed' and ramp_ref = 'hold_123' and jsonb_array_length(original_schedule) = 1 from app.holds where case_id = c1 and released_at is null), 'hold row';
  assert exists (select 1 from app.outbox where case_id = c1 and command = 'select_number'), 'select_number queued';

  -- Idempotency: a retry of the same event returns the stored result and writes no evidence.
  select count(*) into v_before from audit.evidence_events where case_id = c1;
  r := app.apply_case_event(c1, 'hold_confirmed', pg_temp.sys(), c1 || ':hold:v1:confirmed', '{"readback_ok": true}');
  assert r ->> 'status' = 'already_applied' and r ->> 'to_state' = 'held', 'replay';
  assert (select count(*) from audit.evidence_events where case_id = c1) = v_before, 'replay wrote evidence';

  -- Out-of-order and impossible events are refused.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'decision_made', pg_temp.sys(), 'x2', '{"outcome": "verified"}') $q$, c1), '23514', '%no transition from held%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'nonsense', pg_temp.sys(), 'x3', '{}') $q$, c1), '23514');

  -- Number: the Trust Ledger must name a contact point.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'number_resolved', pg_temp.sys(), 'x4', '{}') $q$, c1), '23514', '%guard failed%');
  r := app.apply_case_event(c1, 'number_resolved', pg_temp.sys(), c1 || ':select:v1:resolved',
         jsonb_build_object('contact_point_id', gen_random_uuid(), 'number_selection_id', gen_random_uuid(), 'rule_version', 'sel-v1'));
  assert r ->> 'to_state' = 'calling', r::text;
  assert (select call_attempts = 1 from app.cases where id = c1), 'attempt 1';
  assert exists (select 1 from app.outbox where case_id = c1 and command = 'place_call' and idempotency_key = c1 || ':call:1'), 'call 1 queued';

  -- Call 1: nobody answered, tries left -> retry_wait with a timer.
  r := app.apply_case_event(c1, 'call_ended', pg_temp.sys(), c1 || ':call:1:ended',
         jsonb_build_object('call_id', gen_random_uuid(), 'result', 'no_answer', 'attempt', 1));
  assert r ->> 'to_state' = 'retry_wait', r::text;
  assert exists (select 1 from app.timers where case_id = c1 and kind = 'retry_call' and status = 'scheduled'), 'retry timer';

  -- Timer fires inside the window -> calling again, attempt 2.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'retry_timer_fired', pg_temp.sys(), 'x5', '{"in_window": false}') $q$, c1), '23514');
  r := app.apply_case_event(c1, 'retry_timer_fired', pg_temp.sys(), c1 || ':retry:1', '{"in_window": true, "timer_id": 1}');
  assert r ->> 'to_state' = 'calling' and (select call_attempts = 2 from app.cases where id = c1), 'attempt 2';

  -- Call 2: answers recorded -> deciding, decide command queued.
  r := app.apply_case_event(c1, 'call_ended', pg_temp.sys(), c1 || ':call:2:ended',
         jsonb_build_object('call_id', gen_random_uuid(), 'result', 'answers_recorded', 'attempt', 2,
                            'answers', jsonb_build_object('requested_change', 'yes', 'last_four', 'match')));
  assert r ->> 'to_state' = 'deciding', r::text;
  assert exists (select 1 from app.outbox where case_id = c1 and command = 'decide'), 'decide queued';

  -- Decision: verified -> awaiting_release; the decision row is stored with its versions.
  r := app.apply_case_event(c1, 'decision_made', pg_temp.sys(), c1 || ':decide:v1:2',
         jsonb_build_object('outcome', 'verified', 'rules_fired', jsonb_build_array('V1', 'V2'), 'inputs_hash', repeat('a', 64),
                            'engine_version', 'dec-0.1', 'last_four_result', 'match'));
  assert r ->> 'to_state' = 'awaiting_release', r::text;
  assert (select outcome = 'verified' from app.cases where id = c1), 'case outcome';
  assert (select rules_fired = array['V1', 'V2'] and engine_version = 'dec-0.1' and voided_at is null from app.decisions where case_id = c1), 'decision row';

  -- Release needs a person. The system cannot do it; a server key cannot do it.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.sys(), 'x6', '{"reason": "ok", "expected_version": 1}') $q$, c1), '42501', '%may not be made by the system%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 1, 'approve', 'ok') $q$, c1), '42501', '%sign in first%');

  -- The clerk who entered the change cannot release it (R5).
  perform pg_temp.sign_in(f.clerk);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 1, 'approve', 'looks fine') $q$, c1), '42501', '%approver role required%');
  insert into app.user_roles (user_id, role) values (f.clerk, 'approver');   -- even as an approver
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 1, 'approve', 'looks fine') $q$, c1), '23514', '%cannot release it (R5)%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), 'x7', '{"reason": "ok", "expected_version": 1}') $q$, c1, f.clerk), '23514', '%guard failed%');

  -- An approver: wrong version refused; impersonation refused; a signed-in user cannot act as the system.
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 0, 'approve', 'ok') $q$, c1), '40001');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), 'x8', '{"reason": "ok", "expected_version": 1}') $q$, c1, f.approver2), '42501', '%signed-in user%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'hold_release_confirmed', pg_temp.sys(), 'x9', '{"readback_ok": true}') $q$, c1), '42501', '%cannot act as the system%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 1, 'approve', '   ') $q$, c1), '23514', '%written reason%');

  -- The right approver releases.
  r := app.release_case(c1, 1, 'approve', 'Vendor confirmed on a number with two sources; digits match.');
  assert r ->> 'status' = 'applied' and r ->> 'to_state' = 'releasing', r::text;
  assert (select count(*) = 1 from app.releases where case_id = c1 and action = 'approve' and actor_id = f.approver1 and case_version = 1), 'release row';
  assert exists (select 1 from app.outbox where case_id = c1 and command = 'release_hold' and idempotency_key = c1 || ':release:v1'), 'release queued';
  perform pg_temp.sign_out();

  -- Read-back shows no hold -> released, the only finished state.
  r := app.apply_case_event(c1, 'hold_release_confirmed', pg_temp.sys(), c1 || ':release:v1:confirmed', '{"readback_ok": true}');
  assert r ->> 'to_state' = 'released', r::text;
  assert (select closed_at is not null from app.cases where id = c1), 'closed';
  assert (select status = 'released' and released_at is not null from app.holds where case_id = c1), 'hold released';
  assert not exists (select 1 from app.timers where case_id = c1 and status = 'scheduled'), 'timers cancelled';
  assert (select count(*) from audit.evidence_events where case_id = c1) = 9, 'nine events in the chain';
  assert (select ok from audit.verify_chain(f.chain_id)), 'chain intact';
  assert (select count(*) from audit.evidence_events e where e.case_id = c1 and e.actor_kind = 'person' and e.actor_id = f.approver1) = 1, 'approval in the chain';

  -- After release a new change opens a new case.
  v_change := pg_temp.new_change('000005555', f.clerk);
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'poll:000005555', jsonb_build_object('change_event_id', v_change));
  c2 := (r ->> 'case_id')::uuid;
  assert c2 <> c1 and r ->> 'to_state' = 'open', 'new case';

  -- A change during an open case joins it: back to held, version 2, decision void, old commands dropped.
  perform app.apply_case_event(c2, 'hold_confirmed', pg_temp.sys(), c2 || ':hold:v1:confirmed', '{"readback_ok": true}');
  perform app.apply_case_event(c2, 'number_resolved', pg_temp.sys(), c2 || ':select:v1', jsonb_build_object('contact_point_id', gen_random_uuid()));
  perform app.apply_case_event(c2, 'call_ended', pg_temp.sys(), c2 || ':call:1:ended', '{"result": "answers_recorded"}');
  perform app.apply_case_event(c2, 'decision_made', pg_temp.sys(), c2 || ':decide:v1:1',
            jsonb_build_object('outcome', 'verified', 'inputs_hash', repeat('b', 64), 'engine_version', 'dec-0.1'));
  assert pg_temp.state_of(c2) = 'awaiting_release', 'c2 awaiting release';
  v_change := pg_temp.new_change('000006666', null);                     -- vendor-supplied: unknown changer
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'poll:000006666', jsonb_build_object('change_event_id', v_change));
  assert (r ->> 'case_id')::uuid = c2 and r ->> 'to_state' = 'held' and (r ->> 'version')::int = 2, r::text;
  assert (select outcome is null and call_attempts = 0 and has_unknown_changer and changer_ids = array[f.clerk] from app.cases where id = c2), 'generation 2';
  assert (select voided_at is not null from app.decisions where case_id = c2 and case_version = 1), 'decision voided';
  assert exists (select 1 from app.outbox where case_id = c2 and command = 'place_hold' and idempotency_key = c2 || ':hold:v2'), 'hold re-queued';

  -- A stale approval against version 1 is refused.
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 1, 'approve', 'ok') $q$, c2), '40001');
  perform pg_temp.sign_out();

  -- Three calls with nobody home -> needs_review, hold still on.
  perform app.apply_case_event(c2, 'number_resolved', pg_temp.sys(), c2 || ':select:v2', jsonb_build_object('contact_point_id', gen_random_uuid()));
  perform app.apply_case_event(c2, 'call_ended', pg_temp.sys(), c2 || ':v2:call:1:ended', '{"result": "voicemail"}');
  perform app.apply_case_event(c2, 'retry_timer_fired', pg_temp.sys(), c2 || ':v2:retry:1', '{"in_window": true}');
  perform app.apply_case_event(c2, 'call_ended', pg_temp.sys(), c2 || ':v2:call:2:ended', '{"result": "no_answer"}');
  perform app.apply_case_event(c2, 'retry_timer_fired', pg_temp.sys(), c2 || ':v2:retry:2', '{"in_window": true}');
  r := app.apply_case_event(c2, 'call_ended', pg_temp.sys(), c2 || ':v2:call:3:ended', '{"result": "no_answer"}');
  assert r ->> 'to_state' = 'needs_review', r::text;
  assert (select released_at is null from app.holds where case_id = c2), 'hold still on';

  -- Review: the changer cannot clear it; an approver with a reason can, and becomes the reviewer.
  perform pg_temp.sign_in(f.clerk);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'review_cleared', pg_temp.person(%L), 'x10', '{"reason": "I called them", "expected_version": 2}') $q$, c2, f.clerk), '23514', '%guard failed%');
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'review_cleared', pg_temp.person(%L), 'x11', '{"reason": "", "expected_version": 2}') $q$, c2, f.approver1), '23514');
  r := app.apply_case_event(c2, 'review_cleared', pg_temp.person(f.approver1), c2 || ':v2:review', '{"reason": "Reached the CFO on the contract number; confirmed.", "expected_version": 2}');
  assert r ->> 'to_state' = 'awaiting_release' and (select reviewed_by = f.approver1 from app.cases where id = c2), 'reviewed';

  -- Unknown changer (ADR-10): two approvers, neither the reviewer, neither twice.
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 2, 'approve', 'ok') $q$, c2), '23514', '%reviewer%');
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c2, 2, 'approve', 'Evidence reviewed.');
  assert r ->> 'status' = 'awaiting_second_approver' and pg_temp.state_of(c2) = 'awaiting_release', r::text;
  r := app.release_case(c2, 2, 'approve', 'again');                 -- the same approver again: a replay, not a second approval
  assert r ->> 'status' = 'already_applied' and pg_temp.state_of(c2) = 'awaiting_release', r::text;
  assert (select count(*) = 1 from app.releases where case_id = c2 and case_version = 2 and actor_id = f.approver2), 'no double approval';
  perform pg_temp.sign_in(f.approver3);
  r := app.release_case(c2, 2, 'approve', 'Second approval.');
  assert r ->> 'to_state' = 'releasing', r::text;
  assert (select count(*) = 2 from app.releases where case_id = c2 and case_version = 2 and action = 'approve'), 'two approvals';
  perform pg_temp.sign_out();

  assert (select ok from audit.verify_chain(f.chain_id)), 'chain intact at the end';
end
$$;

rollback;
