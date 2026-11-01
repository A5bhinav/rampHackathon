-- 003: the tables behind the one door stay closed, and the release trigger holds on its own.
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f  pg_temp.fx%rowtype;
  r  jsonb;
  c1 uuid;
begin
  f := pg_temp.setup_fixture();
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_1:detected', jsonb_build_object('change_event_id', f.change1));
  c1 := (r ->> 'case_id')::uuid;

  -- The worker (service role) writes plumbing, never cases, decisions, releases or receipts.
  set local role service_role;
  insert into app.alerts (dataset_id, case_id, kind, severity, message) values (f.dataset, c1, 'test', 'info', 'worker can write plumbing');
  perform pg_temp.expect_error(format($q$ update app.cases set state = 'released' where id = %L $q$, c1), '42501');
  perform pg_temp.expect_error(format($q$ insert into app.decisions (dataset_id, case_id, case_version, outcome, inputs_hash, engine_version) values (%L, %L, 1, 'verified', repeat('c', 64), 'x') $q$, f.dataset, c1), '42501');
  perform pg_temp.expect_error(format($q$ insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason) values (%L, %L, 1, %L, 'approve', 'x') $q$, f.dataset, c1, f.approver1), '42501');
  perform pg_temp.expect_error($q$ delete from app.case_event_receipts $q$, '42501');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, 1, 'approve', 'x') $q$, c1), '42501');

  -- A signed-in user reads everything and writes nothing directly.
  set local role authenticated;
  perform pg_temp.sign_in(f.approver1);
  assert (select count(*) from app.cases where id = c1) = 1, 'authenticated can read cases';
  assert (select count(*) from audit.evidence_events where case_id = c1) = 1, 'authenticated can read evidence';
  perform pg_temp.expect_error(format($q$ update app.cases set state = 'released' where id = %L $q$, c1), '42501');
  perform pg_temp.expect_error(format($q$ insert into app.outbox (dataset_id, case_id, command, idempotency_key) values (%L, %L, 'notify', 'k') $q$, f.dataset, c1), '42501');
  perform pg_temp.expect_error(format($q$ select audit.append_evidence(%L, '{}') $q$, f.chain_id), '42501');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'hold_confirmed', pg_temp.sys(), 'k2', '{"readback_ok": true}') $q$, c1), '42501', '%cannot act as the system%');
  perform pg_temp.sign_out();

  -- anon has no way in at all.
  set local role anon;
  perform pg_temp.expect_error(format($q$ select count(*) from app.cases where id = %L $q$, c1), '42501');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'hold_confirmed', pg_temp.sys(), 'k3', '{}') $q$, c1), '42501');
  reset role;

  -- The releases trigger holds even for the owner writing directly (R5).
  perform pg_temp.expect_error(format($q$ insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason) values (%L, %L, 1, %L, 'approve', 'self-release') $q$, f.dataset, c1, f.clerk), '23514', '%R5%');
  perform pg_temp.expect_error(format($q$ insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason) values (%L, %L, 7, %L, 'approve', 'stale') $q$, f.dataset, c1, f.approver1), '23514', '%version%');
  perform pg_temp.expect_error(format($q$ insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason) values (%L, %L, 1, %L, 'approve', '') $q$, f.dataset, c1, f.approver1), '23514');

  -- One unfinished case per vendor is a constraint, not just a guard.
  perform pg_temp.expect_error(format($q$ insert into app.cases (dataset_id, vendor_id, change_event_id, policy_version) values (%L, %L, %L, 'v0') $q$, f.dataset, f.vendor, f.change1), '23505');

  -- Bank accounts never carry a full number: the last four and a fingerprint only.
  perform pg_temp.expect_error(format($q$ insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint) values (%L, %L, '1', '12345', repeat('d', 64)) $q$, f.dataset, f.vendor), '23514');
  perform pg_temp.expect_error(format($q$ insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint) values (%L, %L, '1', '1234', 'not-a-hash') $q$, f.dataset, f.vendor), '23514');
end
$$;

rollback;
