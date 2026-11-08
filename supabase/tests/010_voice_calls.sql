-- 010: the voice call record (spec 0012, D9). voice.calls is the call the core pins as the
-- case's current call; voice.call_answers never holds account digits, only a digit count or
-- dont_know / refused. The money path owns app.*; this is Sami's voice schema.
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f      pg_temp.fx%rowtype;
  r      jsonb;
  c      uuid;
  v_call uuid;
begin
  f := pg_temp.setup_fixture();
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_1:detected',
         jsonb_build_object('change_event_id', f.change1));
  c := (r ->> 'case_id')::uuid;

  -- A call record inserts and returns its id (the call_id the core pins from call_started).
  insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, contact_point_id, agent_version)
    values (f.dataset, c, 1, c || ':call:v1:1', gen_random_uuid(), 'agent-0.1.0')
    returning id into v_call;
  assert (select status = 'placing' and direction = 'outbound' and provider = 'text' and result is null
            from voice.calls where id = v_call), 'defaults set, live call has no result';

  -- The Idempotency-Key is unique: a replay returns the same call, never a second one (internal.yaml, D6).
  perform pg_temp.expect_error(format($q$
    insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, agent_version)
    values (%L, %L, 1, %L, 'agent-0.1.0') $q$, f.dataset, c, c || ':call:v1:1'), '23505');

  -- case_id must be a real case (money path owns app.cases).
  perform pg_temp.expect_error(format($q$
    insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, agent_version)
    values (%L, gen_random_uuid(), 1, 'k:case', 'agent-0.1.0') $q$, f.dataset), '23503');

  -- attempt is 1..3 (internal.yaml PlaceCallRequest).
  perform pg_temp.expect_error(format($q$
    insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, agent_version)
    values (%L, %L, 4, 'k:att', 'agent-0.1.0') $q$, f.dataset, c), '23514');

  -- result, when set, is one of CallEndedBody.result (evidence-event.schema.json).
  perform pg_temp.expect_error(format($q$
    insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, agent_version, result)
    values (%L, %L, 1, 'k:res', 'agent-0.1.0', 'bogus') $q$, f.dataset, c), '23514');
  update voice.calls set status = 'ended', result = 'answers_recorded', ended_at = now() where id = v_call;
  assert (select status = 'ended' and result = 'answers_recorded' from voice.calls where id = v_call),
    'call ends with a valid result';

  -- answered_by is human or machine only.
  perform pg_temp.expect_error(format($q$
    update voice.calls set answered_by = 'robot' where id = %L $q$, v_call), '23514');

  -- A spoken text answer stores its text and its transcript turn.
  insert into voice.call_answers (call_id, slot, value, turn_id, tool_call_id, capture_method, captured_at)
    values (v_call, 'company_named', 'Acme Fasteners', 2, 'tc_01', 'speech', now());

  -- D9, the point of this schema: account digits are NEVER stored. The last four as digits is refused.
  perform pg_temp.expect_error(format($q$
    insert into voice.call_answers (call_id, slot, value, turn_id, tool_call_id, capture_method, captured_at)
    values (%L, 'account_last4', '4821', 9, 'dtmf_01', 'keypad', now()) $q$, v_call), '23514');

  -- The last four are kept as a count only (keypad) ...
  insert into voice.call_answers (call_id, slot, digit_count, turn_id, tool_call_id, capture_method, captured_at)
    values (v_call, 'account_last4', 4, 9, 'dtmf_01', 'keypad', now());
  -- ... or as dont_know / refused when the callee gave none.
  insert into voice.call_answers (call_id, slot, value, turn_id, tool_call_id, capture_method, captured_at)
    values (v_call, 'account_last4', 'refused', 11, 'tc_07', 'speech', now());

  -- A count outside 1..4, a count on a text slot, and a text slot with no value are all refused.
  perform pg_temp.expect_error(format($q$
    insert into voice.call_answers (call_id, slot, digit_count, turn_id, tool_call_id, capture_method, captured_at)
    values (%L, 'account_last4', 5, 9, 'dtmf_02', 'keypad', now()) $q$, v_call), '23514');
  perform pg_temp.expect_error(format($q$
    insert into voice.call_answers (call_id, slot, value, digit_count, turn_id, tool_call_id, capture_method, captured_at)
    values (%L, 'department', 'AP', 2, 3, 'tc_02', 'speech', now()) $q$, v_call), '23514');
  perform pg_temp.expect_error(format($q$
    insert into voice.call_answers (call_id, slot, turn_id, tool_call_id, capture_method, captured_at)
    values (%L, 'department', 4, 'tc_03', 'speech', now()) $q$, v_call), '23514');

  -- Unknown slot and unknown capture_method are refused (the model cannot invent either).
  perform pg_temp.expect_error(format($q$
    insert into voice.call_answers (call_id, slot, value, turn_id, tool_call_id, capture_method, captured_at)
    values (%L, 'ssn', '123456789', 2, 'tc_04', 'speech', now()) $q$, v_call), '23514');
  perform pg_temp.expect_error(format($q$
    insert into voice.call_answers (call_id, slot, value, turn_id, tool_call_id, capture_method, captured_at)
    values (%L, 'department', 'AP', 2, 'tc_05', 'fax', now()) $q$, v_call), '23514');

  -- offered_number legitimately holds a phone number; it is kept as heard and never dialed.
  insert into voice.call_answers (call_id, slot, value, turn_id, tool_call_id, capture_method, captured_at)
    values (v_call, 'offered_number', '+14155550137', 13, 'tc_08', 'speech', now());

  -- Answers belong to a call and go when it does.
  delete from voice.calls where id = v_call;
  assert (select count(*) = 0 from voice.call_answers where call_id = v_call), 'answers cascade with the call';

  -- Both tables keep row-level security on: the dashboard reads, the worker writes as service_role.
  assert (select relrowsecurity from pg_class where oid = 'voice.calls'::regclass), 'voice.calls has RLS on';
  assert (select relrowsecurity from pg_class where oid = 'voice.call_answers'::regclass), 'voice.call_answers has RLS on';
end
$$;

rollback;
