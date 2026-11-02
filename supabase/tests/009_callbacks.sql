-- 009: a callback the vendor asked for (spec 0007). With tries left the case waits at the
-- time asked; it is an attempt like any other, so the third one goes to a person with the hold
-- still on; and only the current call can ask for one.
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f      pg_temp.fx%rowtype;
  r      jsonb;
  c      uuid;
  call_1 uuid;
  call_2 uuid;
  call_3 uuid;
  v_at   timestamptz := now() + interval '3 hours';
begin
  f := pg_temp.setup_fixture();
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_cb:detected', jsonb_build_object('change_event_id', f.change1));
  c := (r ->> 'case_id')::uuid;
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), c || ':hold:v1:confirmed', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'number_resolved', pg_temp.sys(), c || ':select:v1:resolved', jsonb_build_object('contact_point_id', gen_random_uuid()));
  call_1 := pg_temp.start_call(c);

  -- A callback with no time is refused by the referee too, not only by the core's API.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'call_ended', pg_temp.sys(), 'no-time', '{"call_id": "%s", "result": "callback_requested", "attempt": 1}') $q$,
                                      c, call_1), '23514', '%guard failed%');
  -- A callback from a call that isn't current is refused.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'call_ended', pg_temp.sys(), 'stray', '{"call_id": "%s", "result": "callback_requested", "attempt": 1, "retry_at": "%s"}') $q$,
                                      c, gen_random_uuid(), v_at), '23514', '%guard failed%');

  -- Call 1: "call me back in three hours". The case waits, with a timer at the time asked.
  r := app.apply_case_event(c, 'call_ended', pg_temp.sys(), c || ':call:' || call_1 || ':ended',
         jsonb_build_object('call_id', call_1, 'result', 'callback_requested', 'attempt', 1, 'retry_at', v_at));
  assert r ->> 'to_state' = 'retry_wait', r::text;
  assert (select fire_at = v_at and (payload ->> 'after_attempt')::int = 1 from app.timers
           where case_id = c and kind = 'retry_call' and status = 'scheduled'), 'the redial is at the time the vendor asked for';
  assert (select body_jcs like '%"result":"callback_requested"%' from audit.evidence_events
           where case_id = c and type = 'call_ended'), 'the callback is evidence';

  -- The timer fires inside the window: attempt 2, same version, a new call key.
  r := app.apply_case_event(c, 'retry_timer_fired', pg_temp.sys(), c || ':retry:1', '{"in_window": true}');
  assert r ->> 'to_state' = 'calling' and (select call_attempts = 2 from app.cases where id = c), r::text;
  assert exists (select 1 from app.outbox where idempotency_key = c || ':call:v1:2'), 'attempt 2 queued';
  call_2 := pg_temp.start_call(c);

  -- Call 2 asks again; call 3 asks a third time, which is past the cap: a person takes over.
  perform app.apply_case_event(c, 'call_ended', pg_temp.sys(), c || ':call:' || call_2 || ':ended',
            jsonb_build_object('call_id', call_2, 'result', 'callback_requested', 'attempt', 2, 'retry_at', v_at));
  perform app.apply_case_event(c, 'retry_timer_fired', pg_temp.sys(), c || ':retry:2', '{"in_window": true}');
  call_3 := pg_temp.start_call(c);
  r := app.apply_case_event(c, 'call_ended', pg_temp.sys(), c || ':call:' || call_3 || ':ended',
         jsonb_build_object('call_id', call_3, 'result', 'callback_requested', 'attempt', 3, 'retry_at', v_at));
  assert r ->> 'to_state' = 'needs_review', r::text;
  assert exists (select 1 from app.holds where case_id = c and released_at is null), 'the hold stays on';
  assert not exists (select 1 from app.timers where case_id = c and kind = 'retry_call' and status = 'scheduled'
                       and (payload ->> 'after_attempt')::int = 3), 'no fourth call';

  -- A callback never goes to the decision engine: no decide command was queued.
  assert not exists (select 1 from app.outbox where case_id = c and command = 'decide'), 'nothing to decide';
  assert (select ok from audit.verify_chain(f.chain_id)), 'chain intact';
end
$$;

rollback;
