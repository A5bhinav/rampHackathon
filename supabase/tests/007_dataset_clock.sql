-- 007: one clock per dataset (spec 0002, D2). A lab dataset's simulated time drives every time
-- the functions write; other datasets can't be given a fake time; the clock only moves forward.
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f        pg_temp.fx%rowtype;
  v_chain  uuid;
  v_lab    uuid;
  v_vendor uuid;
  v_old    uuid;
  v_new    uuid;
  v_change uuid;
  v_sim    timestamptz := '2026-11-03 16:00:00+00';
  r        jsonb;
  c        uuid;
begin
  f := pg_temp.setup_fixture();

  -- Only lab datasets have a simulated clock.
  perform pg_temp.expect_error(format($q$ update app.datasets set sim_now = now() where id = %L $q$, f.dataset), '23514');
  perform pg_temp.expect_error(format($q$ select app.advance_clock(%L, now()) $q$, f.dataset), '23514', '%only lab datasets%');
  assert abs(extract(epoch from app.now(f.dataset) - clock_timestamp())) < 5, 'a dev dataset reads the real clock';

  insert into audit.chains (label) values ('lab') returning id into v_chain;
  insert into app.datasets (kind, label, chain_id) values ('lab', 'scenario', v_chain) returning id into v_lab;
  assert app.advance_clock(v_lab, v_sim) = v_sim, 'clock set';
  assert app.now(v_lab) = v_sim, 'lab reads its own clock';
  perform pg_temp.expect_error(format($q$ select app.advance_clock(%L, %L::timestamptz) $q$, v_lab, v_sim - interval '1 minute'), '23514', '%only moves forward%');

  -- A case in the lab dataset lives on simulated time.
  insert into app.vendors (dataset_id, ramp_vendor_id, legal_name) values (v_lab, 'ven_lab', 'Lab Vendor LLC') returning id into v_vendor;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint)
    values (v_lab, v_vendor, '021000021', '1234', repeat('1', 64)) returning id into v_old;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint)
    values (v_lab, v_vendor, '121000248', '9876', repeat('2', 64)) returning id into v_new;
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id)
    values (v_lab, v_vendor, v_old, v_new, 'webhook', 'lab-evt') returning id into v_change;

  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'lab-evt:detected', jsonb_build_object('change_event_id', v_change));
  c := (r ->> 'case_id')::uuid;
  assert (select opened_at = v_sim from app.cases where id = c), 'opened on the simulated clock';
  -- Tuesday 16:00 plus three business days is Friday 16:00.
  assert (select deadline_at = '2026-11-06 16:00:00+00' from app.cases where id = c), 'deadline in simulated business days';
  assert (select fire_at = '2026-11-06 16:00:00+00' from app.timers where case_id = c and kind = 'deadline'), 'deadline timer on simulated time';
  assert (select run_after = v_sim from app.outbox where case_id = c and command = 'place_hold'), 'command due on simulated time';
  assert (select occurred_at = v_sim and recorded_at <> v_sim from audit.evidence_events where case_id = c), 'evidence keeps both clocks';
  assert (select body_jcs like '%"occurred_at":"2026-11-03T16:00:00.000000Z"%' from audit.evidence_events where case_id = c), 'hashed time is simulated';

  -- Moving the clock moves the next event.
  perform app.advance_clock(v_lab, v_sim + interval '90 seconds');
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), c || ':hold:v1:confirmed', '{"readback_ok": true}');
  assert (select confirmed_at = v_sim + interval '90 seconds' from app.holds where case_id = c), 'hold confirmed on simulated time';
  assert (select ok from audit.verify_chain(v_chain)), 'lab chain intact';
end
$$;

rollback;
