-- 006: a second change arrives mid-call (spec 0002, D6 and D8). The case re-holds at version
-- 2, the first schedule survives, the new generation gets its own call keys, and a report for
-- the old generation's call is refused at every point.
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f        pg_temp.fx%rowtype;
  r        jsonb;
  c        uuid;
  call_a   uuid;
  call_b   uuid;
  v_change uuid;
  v_decide jsonb;
begin
  f := pg_temp.setup_fixture();
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_1:detected', jsonb_build_object('change_event_id', f.change1));
  c := (r ->> 'case_id')::uuid;
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), c || ':hold:v1:confirmed',
            jsonb_build_object('readback_ok', true, 'ramp_ref', 'hold_1',
                               'original_schedule', jsonb_build_array(jsonb_build_object('bill_id', 'bill_1', 'payment_date', '2026-11-05'))));
  perform app.apply_case_event(c, 'number_resolved', pg_temp.sys(), c || ':select:v1:resolved', jsonb_build_object('contact_point_id', gen_random_uuid()));
  assert exists (select 1 from app.outbox where case_id = c and idempotency_key = c || ':call:v1:1' and status = 'pending'), 'v1 call queued';
  call_a := pg_temp.start_call(c);
  assert (select current_call_id = call_a from app.cases where id = c), 'call A pinned';

  -- Another change while calling: back to held, version 2, the current call is cleared.
  v_change := pg_temp.new_change('000007777', f.clerk);
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'poll:000007777', jsonb_build_object('change_event_id', v_change));
  assert (r ->> 'case_id')::uuid = c and r ->> 'to_state' = 'held' and (r ->> 'version')::int = 2, r::text;
  assert (select current_call_id is null and call_attempts = 0 from app.cases where id = c), 'generation 2 has no call yet';
  assert (select status = 'failed' from app.outbox where idempotency_key = c || ':call:v1:1'), 'v1 call superseded';

  -- The old call's report arrives now: refused, the case is held.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'call_ended', pg_temp.sys(), 'late-a-1', '{"call_id": "%s", "result": "answers_recorded", "attempt": 1}') $q$, c, call_a), '23514', '%no transition from held%');

  -- The re-hold confirms from held (D8). The first schedule wins over cleared dates (D5).
  r := app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), c || ':hold:v2:confirmed',
         jsonb_build_object('readback_ok', true, 'ramp_ref', 'hold_1',
                            'original_schedule', jsonb_build_array(jsonb_build_object('bill_id', 'bill_1', 'payment_date', null))));
  assert r ->> 'from_state' = 'held' and r ->> 'to_state' = 'held', r::text;
  assert (select original_schedule -> 0 ->> 'payment_date' = '2026-11-05' from app.holds where case_id = c and released_at is null), 'first schedule kept';
  assert exists (select 1 from app.outbox where idempotency_key = c || ':select:v2' and status = 'pending'), 'v2 selection queued';

  -- Generation 2's first call gets its own key; it no longer collides with v1's call 1.
  perform app.apply_case_event(c, 'number_resolved', pg_temp.sys(), c || ':select:v2:resolved', jsonb_build_object('contact_point_id', gen_random_uuid()));
  assert exists (select 1 from app.outbox where idempotency_key = c || ':call:v2:1' and status = 'pending'
                   and (payload ->> 'version')::int = 2 and (payload ->> 'attempt')::int = 1), 'v2 call queued';

  -- call_started must name the current attempt.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'call_started', pg_temp.sys(), 'bad-start', '{"call_id": "%s", "attempt": 2}') $q$, c, gen_random_uuid()), '23514', '%guard failed%');
  call_b := pg_temp.start_call(c);

  -- While generation 2 is calling, the old call's report is still refused (it isn't the current call).
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'call_ended', pg_temp.sys(), 'late-a-2', '{"call_id": "%s", "result": "answers_recorded", "attempt": 1}') $q$, c, call_a), '23514', '%guard failed%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'call_ended', pg_temp.sys(), 'late-a-3', '{"call_id": "%s", "result": "no_answer", "attempt": 1}') $q$, c, call_a), '23514', '%guard failed%');

  -- The current call's report moves the case, and the decide command carries the stored body.
  r := app.apply_case_event(c, 'call_ended', pg_temp.sys(), c || ':call:' || call_b || ':ended',
         jsonb_build_object('call_id', call_b, 'result', 'answers_recorded', 'attempt', 1, 'last_four_result', 'match',
                            'checks', jsonb_build_object('disclosure_first', true, 'answers_grounded', true,
                                                         'agent_spoke_account_digits', false, 'new_number_accepted', false,
                                                         'policy_violations', 0, 'speech_guard_trips', 0)));
  assert r ->> 'to_state' = 'deciding', r::text;
  select payload into v_decide from app.outbox where case_id = c and command = 'decide';
  assert v_decide ->> 'last_four_result' = 'match' and (v_decide ->> 'version')::int = 2
     and (v_decide -> 'checks' ->> 'disclosure_first')::boolean and v_decide ->> 'call_id' = call_b::text, v_decide::text;

  -- Both call starts are evidence (the design lists call start), and the chain is intact.
  assert (select count(*) = 2 from audit.evidence_events where case_id = c and type = 'call_started'), 'call starts in the chain';
  assert (select ok from audit.verify_chain(f.chain_id)), 'chain intact';
end
$$;

rollback;
