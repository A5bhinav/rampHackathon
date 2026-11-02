-- 008: a repeated release request answers "already applied" instead of a refusal, and only
-- for the same person's own earlier request.
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
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_1:detected', jsonb_build_object('change_event_id', f.change1));
  c := (r ->> 'case_id')::uuid;
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), c || ':hold:v1:confirmed', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'number_resolved', pg_temp.sys(), c || ':select:v1', jsonb_build_object('contact_point_id', gen_random_uuid()));
  v_call := pg_temp.start_call(c);
  perform app.apply_case_event(c, 'call_ended', pg_temp.sys(), c || ':ended', jsonb_build_object('call_id', v_call, 'result', 'answers_recorded', 'attempt', 1));
  perform app.apply_case_event(c, 'decision_made', pg_temp.sys(), c || ':decided',
            jsonb_build_object('outcome', 'verified', 'inputs_hash', repeat('a', 64), 'engine_version', 'test'));

  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, 1, 'approve', 'Confirmed.');
  assert r ->> 'status' = 'applied' and r ->> 'to_state' = 'releasing', r::text;
  r := app.release_case(c, 1, 'approve', 'Confirmed.');               -- the double click
  assert r ->> 'status' = 'already_applied' and r ->> 'to_state' = 'releasing', r::text;
  assert (select count(*) = 1 from app.releases where case_id = c), 'one release row';

  -- Someone else's request is not a replay of approver1's: it is refused on its merits.
  perform pg_temp.sign_in(f.approver2);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 1, 'approve', 'Me too.') $q$, c), '23514', '%not awaiting_release%');
  -- Nor is a reject by the same person a replay of their approval.
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 1, 'reject', 'Changed my mind.') $q$, c), '23514', '%not awaiting_release%');
  perform pg_temp.sign_out();
end
$$;

rollback;
