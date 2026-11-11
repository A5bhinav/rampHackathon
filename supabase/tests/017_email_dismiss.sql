-- 017: two people can dismiss a blocked email-only case, which lifts the hold (spec 0090).
--
-- An email case never touches Ramp, so when the vendor denies the change the case would sit in
-- blocked for good, freezing the real vendor's real account. Two different approvers, each with
-- a written reason, may dismiss it while the case is email only: every change on it came in by
-- email and Ramp's account on file is still the one the email was compared against.
begin;
\i supabase/tests/_helpers.sql

-- A vendor in the fixture's dataset with one account on file.
create function pg_temp.vendor(p_ref text) returns uuid
language plpgsql as $$
declare
  f pg_temp.fx%rowtype;
  v uuid;
begin
  select * into f from pg_temp.fx limit 1;
  insert into app.vendors (dataset_id, ramp_vendor_id, legal_name) values (f.dataset, p_ref, 'Vendor ' || p_ref)
    returning id into v;
  insert into app.bank_accounts (dataset_id, vendor_id, ramp_account_id, routing_number, account_last4, account_fingerprint, first_seen_at)
    values (f.dataset, v, 'ra_' || p_ref, '021000021', '1111', pg_temp.fingerprint('021000021', p_ref || ':on-file'),
            now() - interval '400 days');
  return v;
end
$$;

create function pg_temp.on_file(p_vendor uuid) returns uuid
language sql as $$
  select id from app.bank_accounts where vendor_id = p_vendor and is_current order by first_seen_at desc, created_at desc limit 1
$$;

-- An email asks to pay a new account, recorded the way the email door does it (door.py): the
-- requested account is never current, the change names the account on file as the old one, and
-- Ramp is untouched.
create function pg_temp.email_change(p_vendor uuid, p_source text, p_by uuid default null) returns uuid
language plpgsql as $$
declare
  f      pg_temp.fx%rowtype;
  v_acct uuid;
  v_id   uuid;
begin
  select * into f from pg_temp.fx limit 1;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint, is_current)
    values (f.dataset, p_vendor, '026009593', right(p_source, 4), pg_temp.fingerprint('026009593', p_source), false)
    returning id into v_acct;
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id,
                                      changed_by, detected_at)
    values (f.dataset, p_vendor, pg_temp.on_file(p_vendor), v_acct, 'email', 'email:' || p_source, p_by, clock_timestamp())
    returning id into v_id;
  return v_id;
end
$$;

-- Someone changes the account in Ramp and detection records it (detection.py): the new account
-- becomes the one on file. Detection reads its clock before it writes, so p_detected_at can be
-- a moment before the case event that follows.
create function pg_temp.ramp_change(p_vendor uuid, p_source text, p_by uuid,
                                    p_detected_at timestamptz default clock_timestamp()) returns uuid
language plpgsql as $$
declare
  f      pg_temp.fx%rowtype;
  v_old  uuid := pg_temp.on_file(p_vendor);
  v_acct uuid;
  v_id   uuid;
begin
  select * into f from pg_temp.fx limit 1;
  update app.bank_accounts set is_current = false where vendor_id = p_vendor and is_current;
  insert into app.bank_accounts (dataset_id, vendor_id, ramp_account_id, routing_number, account_last4, account_fingerprint, is_current)
    values (f.dataset, p_vendor, 'ra_' || p_source, '121000248', right(p_source, 4), pg_temp.fingerprint('121000248', p_source), true)
    returning id into v_acct;
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id,
                                      changed_by, detected_at)
    values (f.dataset, p_vendor, v_old, v_acct, 'webhook', 'ramp-account:' || p_source, p_by, p_detected_at)
    returning id into v_id;
  return v_id;
end
$$;

-- Apply a recorded change the way detection and the email door do; returns the case.
create function pg_temp.detect(p_change uuid) returns uuid
language sql as $$
  select (app.apply_case_event(null, 'change_detected', pg_temp.sys(), p_change || ':detected',
            jsonb_build_object('change_event_id', p_change)) ->> 'case_id')::uuid
$$;

-- Drive a case from open or held through a call to a decision: hold, number, call, p_outcome.
-- Returns the version.
create function pg_temp.call_and_decide(p_case uuid, p_outcome text) returns integer
language plpgsql as $$
declare
  c      app.cases%rowtype;
  v_call uuid;
  k      text;
begin
  select * into c from app.cases where id = p_case;
  k := format('%s:v%s', p_case, c.version);
  if c.state = 'open' then
    perform app.apply_case_event(p_case, 'hold_confirmed', pg_temp.sys(), k || ':hold', '{"readback_ok": true}');
  end if;
  perform app.apply_case_event(p_case, 'number_resolved', pg_temp.sys(), k || ':select',
            jsonb_build_object('contact_point_id', gen_random_uuid()));
  v_call := pg_temp.start_call(p_case);
  perform app.apply_case_event(p_case, 'call_ended', pg_temp.sys(), k || ':ended',
            jsonb_build_object('call_id', v_call, 'result', 'answers_recorded'));
  perform app.apply_case_event(p_case, 'decision_made', pg_temp.sys(), k || ':decided',
            jsonb_build_object('outcome', p_outcome, 'inputs_hash', repeat('c', 64), 'engine_version', 'test'));
  return (select version from app.cases where id = p_case);
end
$$;

-- Drive a case to blocked (Not verified). Returns the version.
create function pg_temp.to_blocked(p_case uuid) returns integer
language plpgsql as $$
declare
  v integer := pg_temp.call_and_decide(p_case, 'not_verified');
begin
  assert pg_temp.state_of(p_case) = 'blocked', format('case %s is %s, not blocked', p_case, pg_temp.state_of(p_case));
  return v;
end
$$;

-- Drive a case to awaiting_release (Verified). Returns the version.
create function pg_temp.to_awaiting_release(p_case uuid) returns integer
language plpgsql as $$
declare
  v integer := pg_temp.call_and_decide(p_case, 'verified');
begin
  assert pg_temp.state_of(p_case) = 'awaiting_release', format('case %s is %s, not awaiting_release', p_case, pg_temp.state_of(p_case));
  return v;
end
$$;

-- The release worker gave up: the release_hold command failed and the case goes to review.
create function pg_temp.release_failed(p_case uuid, p_key text) returns void
language plpgsql as $$
begin
  update app.outbox set status = 'failed', last_error = 'test: Ramp refused', done_at = now()
   where case_id = p_case and command = 'release_hold' and status = 'pending';
  perform app.apply_case_event(p_case, 'command_failed', pg_temp.sys(), p_key, '{"command": "release_hold", "attempts": 5}');
  assert pg_temp.state_of(p_case) = 'needs_review', 'a failed release goes to review';
end
$$;

create function pg_temp.email_only(p_case uuid) returns boolean
language plpgsql as $$
begin
  return (select app.case_email_only(c) from app.cases c where c.id = p_case);
end
$$;

-- 1. Two different approvers dismiss: blocked, then blocked, then releasing, and the hold lifts.
do $$
declare
  f  pg_temp.fx%rowtype;
  vn uuid;
  c  uuid;
  v  integer;
  r  jsonb;
begin
  f := pg_temp.setup_fixture();
  vn := pg_temp.vendor('ven_email');
  c := pg_temp.detect(pg_temp.email_change(vn, '000007777'));
  v := pg_temp.to_blocked(c);
  assert (select has_unknown_changer and changer_ids = '{}' from app.cases where id = c), 'an email case has no known changer';

  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'The vendor never asked for this; Ramp never changed.');
  assert r ->> 'status' = 'awaiting_second_approver' and r ->> 'from_state' = 'blocked' and r ->> 'to_state' = 'blocked', r::text;
  assert pg_temp.state_of(c) = 'blocked', 'one dismissal leaves the case blocked';
  assert not exists (select 1 from app.outbox where case_id = c and command = 'release_hold'), 'no release after one dismissal';

  -- The same approver again is a replay, not a second dismissal.
  r := app.release_case(c, v, 'approve', 'Clicked twice.');
  assert r ->> 'status' = 'already_applied' and pg_temp.state_of(c) = 'blocked', r::text;
  assert (select count(*) from app.releases where case_id = c) = 1, 'one approval row after a double click';

  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c, v, 'approve', 'Checked: Ramp still has the account on file.');
  assert r ->> 'status' = 'applied' and r ->> 'from_state' = 'blocked' and r ->> 'to_state' = 'releasing', r::text;
  assert pg_temp.state_of(c) = 'releasing', 'the second dismissal moves to releasing';

  -- One approval row per approver, at this version, and exactly one release_hold command.
  assert (select count(*) from app.releases where case_id = c) = 2, 'two approval rows';
  assert exists (select 1 from app.releases where case_id = c and case_version = v and action = 'approve' and actor_id = f.approver1), 'approver1 row';
  assert exists (select 1 from app.releases where case_id = c and case_version = v and action = 'approve' and actor_id = f.approver2), 'approver2 row';
  assert (select count(*) from app.outbox where case_id = c and command = 'release_hold') = 1, 'exactly one release_hold';
  assert exists (select 1 from app.outbox where case_id = c and command = 'release_hold' and status = 'pending'
                   and idempotency_key = c || ':release:v' || v), 'release_hold keyed by version';

  -- A third approver has nothing left to do.
  perform pg_temp.sign_in(f.approver3);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Me too.') $q$, c, v), '23514', '%is releasing, not awaiting_release%');
  perform pg_temp.sign_out();

  -- The worker lifts the hold and reads it back: released.
  r := app.apply_case_event(c, 'hold_release_confirmed', pg_temp.sys(), c || ':release:v' || v || ':confirmed', '{"readback_ok": true}');
  assert r ->> 'to_state' = 'released', r::text;
  assert (select status = 'released' and released_at is not null from app.holds where case_id = c), 'hold released';

  -- Both dismissals are in the evidence chain, each a person moving the case out of blocked.
  assert (select count(*) from audit.evidence_events e
           where e.case_id = c and e.actor_kind = 'person' and e.type in ('release_co_approved', 'release_approved')
             and e.body_jcs::jsonb ->> 'from_state' = 'blocked') = 2, 'both dismissals in the chain';
  assert (select ok from audit.verify_chain(f.chain_id)), 'chain intact';
end
$$;

-- 2. Refusals on an email-only case: a clerk, no reason, reject, a stale version, and the first
--    dismisser trying to be the second. Each guard is tried straight through apply_case_event
--    too, since a person may call it directly.
do $$
declare
  f    pg_temp.fx%rowtype;
  c    uuid;
  v    integer;
  body text;
  r    jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.email_change(pg_temp.vendor('ven_refusals'), '000007801'));
  v := pg_temp.to_blocked(c);
  assert pg_temp.email_only(c), 'the case is email only';
  body := format('{"reason": "Ramp never changed.", "expected_version": %s}', v);

  -- As the first dismissal.
  perform pg_temp.sign_in(f.clerk);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '42501', '%approver role required%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_co_approved', pg_temp.person(%L), 'r1', %L) $q$, c, f.clerk, body), '23514', '%guard failed%');
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', '   ') $q$, c, v), '23514', '%written reason%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_co_approved', pg_temp.person(%L), 'r2', %L) $q$,
            c, f.approver1, format('{"reason": " ", "expected_version": %s}', v)), '23514', '%guard failed%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'reject', 'No.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v - 1), '40001');
  -- Straight to release_approved with nobody's approval yet: refused.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), 'r3', %L) $q$, c, f.approver1, body), '23514', '%guard failed%');
  assert pg_temp.state_of(c) = 'blocked' and not exists (select 1 from app.releases where case_id = c), 'nothing moved';

  r := app.release_case(c, v, 'approve', 'Ramp never changed.');
  assert r ->> 'status' = 'awaiting_second_approver', r::text;

  -- As the second dismissal.
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), 'r4', %L) $q$, c, f.approver1, body), '23514', '%guard failed%');
  perform pg_temp.sign_in(f.clerk);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '42501', '%approver role required%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), 'r5', %L) $q$, c, f.clerk, body), '23514', '%guard failed%');
  perform pg_temp.sign_in(f.approver2);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', '') $q$, c, v), '23514', '%written reason%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), 'r6', %L) $q$,
            c, f.approver2, format('{"reason": "", "expected_version": %s}', v)), '23514', '%guard failed%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'reject', 'No.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.sign_out();
  assert pg_temp.state_of(c) = 'blocked' and (select count(*) from app.releases where case_id = c) = 1, 'still one dismissal';
  assert not exists (select 1 from app.outbox where case_id = c and command = 'release_hold'), 'no release queued';
end
$$;

-- 3. A blocked case that a Ramp change opened is refused exactly as before.
do $$
declare
  f pg_temp.fx%rowtype;
  c uuid;
  v integer;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.ramp_change(pg_temp.vendor('ven_ramp'), '000008001', f.clerk));
  v := pg_temp.to_blocked(c);
  assert not pg_temp.email_only(c), 'a Ramp case is not email only';
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Looks fine.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'reject', 'No.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_co_approved', pg_temp.person(%L), 'ramp1', %L) $q$,
            c, f.approver1, format('{"reason": "Looks fine.", "expected_version": %s}', v)), '23514', '%guard failed%');
  perform pg_temp.sign_out();
  assert pg_temp.state_of(c) = 'blocked' and not exists (select 1 from app.releases where case_id = c), 'nothing moved';
end
$$;

-- 4. An email case that a Ramp change joined (then another email): refused, although the current
--    change is an email against the account now on file.
do $$
declare
  f  pg_temp.fx%rowtype;
  vm uuid;
  c  uuid;
  v  integer;
begin
  select * into f from pg_temp.fx limit 1;
  vm := pg_temp.vendor('ven_mixed');
  c := pg_temp.detect(pg_temp.email_change(vm, '000008101'));
  assert pg_temp.detect(pg_temp.ramp_change(vm, '000008102', f.clerk)) = c, 'the Ramp change joins the email case';
  assert pg_temp.detect(pg_temp.email_change(vm, '000008103')) = c, 'a second email joins it too';
  v := pg_temp.to_blocked(c);
  assert (select ce.detected_via = 'email' and ce.old_account_id = pg_temp.on_file(vm)
            from app.cases x join app.bank_change_events ce on ce.id = x.change_event_id where x.id = c),
    'the current change is an email against the account on file';
  assert not pg_temp.email_only(c), 'Ramp changed while the case was open';
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.sign_out();
  assert pg_temp.state_of(c) = 'blocked' and not exists (select 1 from app.releases where case_id = c), 'nothing moved';
end
$$;

-- 5. Rule B alone: a Ramp-detected change for the vendor recorded after the case opened (here one
--    that never joined the case, with the account on file unchanged) is enough to refuse.
do $$
declare
  f  pg_temp.fx%rowtype;
  vb uuid;
  c  uuid;
  v  integer;
  a  uuid;
begin
  select * into f from pg_temp.fx limit 1;
  vb := pg_temp.vendor('ven_late_ramp');
  c := pg_temp.detect(pg_temp.email_change(vb, '000008201'));
  v := pg_temp.to_blocked(c);
  assert pg_temp.email_only(c), 'email only before the Ramp change';
  insert into app.bank_accounts (dataset_id, vendor_id, ramp_account_id, routing_number, account_last4, account_fingerprint, is_current)
    values (f.dataset, vb, 'ra_late', '121000248', '8202', pg_temp.fingerprint('121000248', '000008202'), false)
    returning id into a;
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id, changed_by, detected_at)
    values (f.dataset, vb, pg_temp.on_file(vb), a, 'poll', 'ramp-account:ra_late', f.clerk, clock_timestamp());
  assert not pg_temp.email_only(c), 'a Ramp change since the case opened refuses';
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.sign_out();
end
$$;

-- 6. Rule C: Ramp's account on file must still be the one the email was compared against, and
--    the only current one. The check reads the present, so putting it back allows it again.
do $$
declare
  f      pg_temp.fx%rowtype;
  vc     uuid;
  c      uuid;
  v      integer;
  a_file uuid;
  a2     uuid;
  r      jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  vc := pg_temp.vendor('ven_account_moved');
  a_file := pg_temp.on_file(vc);
  c := pg_temp.detect(pg_temp.email_change(vc, '000008301'));
  v := pg_temp.to_blocked(c);
  assert pg_temp.email_only(c), 'email only to start with';

  -- Two current accounts: which one is on file is unclear, so refuse.
  insert into app.bank_accounts (dataset_id, vendor_id, ramp_account_id, routing_number, account_last4, account_fingerprint, is_current)
    values (f.dataset, vc, 'ra_other', '121000248', '8302', pg_temp.fingerprint('121000248', '000008302'), true)
    returning id into a2;
  assert not pg_temp.email_only(c), 'two current accounts refuse';

  -- The account on file moved with no change event (an older known account made current again).
  update app.bank_accounts set is_current = false where id = a_file;
  assert not pg_temp.email_only(c), 'a different account on file refuses';
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.sign_out();

  update app.bank_accounts set is_current = (id = a_file) where id in (a_file, a2);
  assert pg_temp.email_only(c), 'the account on file is back: email only again';
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Ramp has the account on file again.');
  assert r ->> 'status' = 'awaiting_second_approver', r::text;
  perform pg_temp.sign_out();
end
$$;

-- 7. Rule D alone: a clerk's Ramp change opened the case and an email joined it. The current
--    change is the email, its old account is the one on file, and the Ramp change was detected
--    before the case opened, so only "every change on the case came in by email" catches it.
do $$
declare
  f  pg_temp.fx%rowtype;
  vd uuid;
  c  uuid;
  v  integer;
begin
  select * into f from pg_temp.fx limit 1;
  vd := pg_temp.vendor('ven_ramp_then_email');
  c := pg_temp.detect(pg_temp.ramp_change(vd, '000008401', f.clerk, clock_timestamp() - interval '1 second'));
  assert pg_temp.detect(pg_temp.email_change(vd, '000008402')) = c, 'the email joins the Ramp case';
  v := pg_temp.to_blocked(c);
  assert (select ce.detected_via = 'email' and ce.old_account_id = pg_temp.on_file(vd)
            from app.cases x join app.bank_change_events ce on ce.id = x.change_event_id where x.id = c),
    'rules A and C pass: the current change is an email against the account on file';
  assert not exists (select 1 from app.bank_change_events o join app.cases x on x.id = c
                      where o.vendor_id = vd and o.detected_via <> 'email' and o.detected_at >= x.opened_at),
    'rule B passes: the Ramp change was detected before the case opened';
  assert not pg_temp.email_only(c), 'a Ramp change joined the case';
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_co_approved', pg_temp.person(%L), 'd1', %L) $q$,
            c, f.approver1, format('{"reason": "Ramp never changed.", "expected_version": %s}', v)), '23514', '%guard failed%');
  perform pg_temp.sign_out();
  assert pg_temp.state_of(c) = 'blocked' and not exists (select 1 from app.releases where case_id = c), 'nothing moved';
end
$$;

-- 8. A blocked email case reached through review and a rejected release: the reviewer who
--    cleared it can't dismiss it (R5); two others can, the rejecter among them.
do $$
declare
  f pg_temp.fx%rowtype;
  c uuid;
  v integer;
  r jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.email_change(pg_temp.vendor('ven_reviewed'), '000008501'));
  perform app.apply_case_event(c, 'hold_confirmed', pg_temp.sys(), c || ':hold', '{"readback_ok": true}');
  perform app.apply_case_event(c, 'no_provable_number', pg_temp.sys(), c || ':no_number', '{}');
  v := (select version from app.cases where id = c);
  perform pg_temp.sign_in(f.approver3);
  perform app.apply_case_event(c, 'review_cleared', pg_temp.person(f.approver3), c || ':review',
            format('{"reason": "Reached them on the number on file.", "expected_version": %s}', v)::jsonb);
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c, v, 'reject', 'Not convinced.');
  assert r ->> 'to_state' = 'blocked', r::text;
  assert pg_temp.email_only(c), 'still email only';

  perform pg_temp.sign_in(f.approver3);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%reviewer%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_co_approved', pg_temp.person(%L), 'v1', %L) $q$,
            c, f.approver3, format('{"reason": "Ramp never changed.", "expected_version": %s}', v)), '23514', '%guard failed%');
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Ramp never changed.');
  assert r ->> 'status' = 'awaiting_second_approver', r::text;
  perform pg_temp.sign_in(f.approver3);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%reviewer%');
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c, v, 'approve', 'Ramp never changed; I agree.');
  assert r ->> 'to_state' = 'releasing', r::text;
  perform pg_temp.sign_out();
end
$$;

-- 9. Two approvers even when the changer is known. The email door always records an unknown
--    changer, but the ruling is "always two", so a known one (here the clerk) must not shorten
--    it, and that changer can't dismiss even as an approver (R5).
do $$
declare
  f pg_temp.fx%rowtype;
  c uuid;
  v integer;
  r jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.email_change(pg_temp.vendor('ven_known_changer'), '000008601', f.clerk));
  v := pg_temp.to_blocked(c);
  assert (select not has_unknown_changer and changer_ids = array[f.clerk] from app.cases where id = c), 'a known changer';
  assert pg_temp.email_only(c), 'email only';
  insert into app.user_roles (user_id, role) values (f.clerk, 'approver') on conflict do nothing;
  perform pg_temp.sign_in(f.clerk);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%cannot release it (R5)%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_co_approved', pg_temp.person(%L), 'k1', %L) $q$,
            c, f.clerk, format('{"reason": "Ramp never changed.", "expected_version": %s}', v)), '23514', '%guard failed%');
  delete from app.user_roles where user_id = f.clerk and role = 'approver';
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Ramp never changed.');
  assert r ->> 'status' = 'awaiting_second_approver' and pg_temp.state_of(c) = 'blocked', r::text;
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c, v, 'approve', 'Ramp never changed; agreed.');
  assert r ->> 'to_state' = 'releasing', r::text;
  perform pg_temp.sign_out();
end
$$;

-- 10. The table and the edges: exactly two person moves out of blocked, no reject from blocked,
--    and nothing (not even a null case) passes by accident.
do $$
declare
  f pg_temp.fx%rowtype;
begin
  select * into f from pg_temp.fx limit 1;
  assert (select count(*) from app.case_transitions where from_state = 'blocked' and actor_kind = 'person') = 2, 'two person moves out of blocked';
  assert exists (select 1 from app.case_transitions where from_state = 'blocked' and event_type = 'release_co_approved'
                   and actor_kind = 'person' and guard_key = 'email_dismiss_first' and to_state = 'blocked'), 'first dismissal row';
  assert exists (select 1 from app.case_transitions where from_state = 'blocked' and event_type = 'release_approved'
                   and actor_kind = 'person' and guard_key = 'email_dismiss_four_eyes' and to_state = 'releasing'), 'second dismissal row';
  assert not exists (select 1 from app.case_transitions where from_state = 'blocked' and event_type = 'release_rejected'), 'no reject from blocked';
  assert app.case_email_only(null) = false, 'a null case is not email only';
  assert app.eval_guard('email_dismiss_first', null::app.cases, f.approver1, '{"reason": "x"}') = false, 'first guard on a null case';
  assert app.eval_guard('email_dismiss_four_eyes', null::app.cases, f.approver1, '{"reason": "x"}') = false, 'second guard on a null case';
end
$$;

-- 11. Rule C1 alone: the account the email was compared against is no longer current, and no
--     other account is (C2 has nothing to object to). Refused.
do $$
declare
  f      pg_temp.fx%rowtype;
  vn     uuid;
  a_file uuid;
  c      uuid;
  v      integer;
begin
  select * into f from pg_temp.fx limit 1;
  vn := pg_temp.vendor('ven_no_current');
  a_file := pg_temp.on_file(vn);
  c := pg_temp.detect(pg_temp.email_change(vn, '000008701'));
  v := pg_temp.to_blocked(c);
  assert pg_temp.email_only(c), 'email only to start with';
  update app.bank_accounts set is_current = false where id = a_file;
  assert not exists (select 1 from app.bank_accounts where vendor_id = vn and is_current), 'no current account at all';
  assert not pg_temp.email_only(c), 'the account on file is gone: refused';
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.sign_out();
  assert pg_temp.state_of(c) = 'blocked' and not exists (select 1 from app.releases where case_id = c), 'nothing moved';
end
$$;

-- 12. No account on file when the email came (old_account_id null): never email only, stays blocked.
do $$
declare
  f  pg_temp.fx%rowtype;
  vn uuid;
  c  uuid;
  v  integer;
begin
  select * into f from pg_temp.fx limit 1;
  vn := pg_temp.vendor('ven_no_account');
  update app.bank_accounts set is_current = false where vendor_id = vn;
  c := pg_temp.detect(pg_temp.email_change(vn, '000008801'));
  assert (select ce.old_account_id is null from app.cases x join app.bank_change_events ce on ce.id = x.change_event_id where x.id = c),
    'the email change has no old account';
  v := pg_temp.to_blocked(c);
  assert not pg_temp.email_only(c), 'no account on file: not email only';
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Ramp never changed.') $q$, c, v), '23514', '%is blocked, not awaiting_release%');
  perform pg_temp.sign_out();
  assert pg_temp.state_of(c) = 'blocked' and not exists (select 1 from app.releases where case_id = c), 'nothing moved';
end
$$;

-- 13. Path A: approver1 co-approves the release in awaiting_release, approver2 rejects it, and the
--     case is blocked at the same version. Only approvals made while blocked count, so the
--     earlier co-approval is not a first dismissal, and approver1's own earlier receipt does not
--     answer approver1's dismissal.
do $$
declare
  f    pg_temp.fx%rowtype;
  c    uuid;
  v    integer;
  r    jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.email_change(pg_temp.vendor('ven_path_a'), '000009101'));
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'The vendor confirmed.');
  assert r ->> 'status' = 'awaiting_second_approver', r::text;
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c, v, 'reject', 'Not convinced.');
  assert r ->> 'to_state' = 'blocked' and (select version from app.cases where id = c) = v, r::text;

  -- One person can't lift the hold on the strength of the earlier co-approval, even directly.
  perform pg_temp.sign_in(f.approver3);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), 'a1', %L) $q$,
            c, f.approver3, format('{"reason": "Ramp never changed.", "expected_version": %s}', v)), '23514', '%guard failed%');

  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Ramp never changed.');
  assert r ->> 'status' = 'awaiting_second_approver' and r ->> 'from_state' = 'blocked' and pg_temp.state_of(c) = 'blocked', r::text;
  r := app.release_case(c, v, 'approve', 'Clicked twice.');
  assert r ->> 'status' = 'already_applied' and pg_temp.state_of(c) = 'blocked', r::text;
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c, v, 'approve', 'Ramp never changed; agreed.');
  assert r ->> 'status' = 'applied' and r ->> 'to_state' = 'releasing', r::text;
  assert (select count(*) from app.outbox where case_id = c and command = 'release_hold') = 1, 'exactly one release_hold';
  perform pg_temp.sign_out();
end
$$;

-- 14. Path B: after the reject, a third approver's stale Approve (the version didn't change) is
--     a first dismissal only; a second, different person is still needed.
do $$
declare
  f pg_temp.fx%rowtype;
  c uuid;
  v integer;
  r jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.email_change(pg_temp.vendor('ven_path_b'), '000009201'));
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'The vendor confirmed.');
  assert r ->> 'status' = 'awaiting_second_approver', r::text;
  perform pg_temp.sign_in(f.approver2);
  perform app.release_case(c, v, 'reject', 'Not convinced.');
  assert pg_temp.state_of(c) = 'blocked', 'rejected to blocked';

  perform pg_temp.sign_in(f.approver3);
  r := app.release_case(c, v, 'approve', 'Looks fine to me.');           -- loaded before the reject
  assert r ->> 'status' = 'awaiting_second_approver' and pg_temp.state_of(c) = 'blocked', r::text;
  r := app.release_case(c, v, 'approve', 'Looks fine to me.');
  assert r ->> 'status' = 'already_applied' and pg_temp.state_of(c) = 'blocked', r::text;
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Ramp never changed.');
  assert r ->> 'to_state' = 'releasing', r::text;
  perform pg_temp.sign_out();
end
$$;

-- 15. Path C: a release that two people approved fails, a reviewer blocks the case, and it is
--     blocked at the same version with both approvals still on file. Neither counts: the first
--     approval after the block is a first dismissal. Then the same again after a completed
--     dismissal: each blocked spell starts from nothing.
do $$
declare
  f pg_temp.fx%rowtype;
  c uuid;
  v integer;
  r jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.email_change(pg_temp.vendor('ven_path_c'), '000009301'));
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.sign_in(f.approver1);
  perform app.release_case(c, v, 'approve', 'The vendor confirmed.');
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c, v, 'approve', 'Agreed.');
  assert r ->> 'to_state' = 'releasing', r::text;
  perform pg_temp.sign_out();
  perform pg_temp.release_failed(c, c || ':failed:1');
  perform pg_temp.sign_in(f.approver3);
  perform app.apply_case_event(c, 'review_blocked', pg_temp.person(f.approver3), c || ':blocked:1',
            format('{"reason": "Hold it until we know why Ramp refused.", "expected_version": %s}', v)::jsonb);
  assert pg_temp.state_of(c) = 'blocked' and (select version from app.cases where id = c) = v, 'blocked at the same version';
  assert pg_temp.email_only(c), 'still email only';

  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Ramp never changed.');
  assert r ->> 'status' = 'awaiting_second_approver' and pg_temp.state_of(c) = 'blocked', r::text;
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(c, v, 'approve', 'Ramp never changed; agreed.');
  assert r ->> 'status' = 'applied' and r ->> 'to_state' = 'releasing', r::text;
  -- Pre-existing (spec 0090, for Abhi): release_hold is keyed {case}:release:v{n}, and that key was
  -- used by the failed release, so this second release queues no command. The case waits in
  -- releasing with the hold on (fails closed). Change this assertion when that is fixed.
  assert (select count(*) from app.outbox where case_id = c and command = 'release_hold') = 1
     and not exists (select 1 from app.outbox where case_id = c and command = 'release_hold' and status = 'pending'),
    'known issue: no new release_hold for a second release at the same version';

  -- The dismissal's release fails too, and the case is blocked again: a new spell.
  perform pg_temp.sign_out();
  perform pg_temp.release_failed(c, c || ':failed:2');
  perform pg_temp.sign_in(f.approver3);
  perform app.apply_case_event(c, 'review_blocked', pg_temp.person(f.approver3), c || ':blocked:2',
            format('{"reason": "Blocked again.", "expected_version": %s}', v)::jsonb);
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Ramp never changed.');
  assert r ->> 'status' = 'awaiting_second_approver' and pg_temp.state_of(c) = 'blocked', r::text;
  perform pg_temp.sign_in(f.approver3);
  r := app.release_case(c, v, 'approve', 'Ramp never changed; agreed.');
  assert r ->> 'to_state' = 'releasing', r::text;
  perform pg_temp.sign_out();
end
$$;

-- 16. The approvals are read from the receipts, which only apply_case_event writes. A forged
--     evidence row (the service role may insert evidence) saying approver2 dismissed does not let
--     approver1 dismiss alone, and the service role cannot write a receipt.
do $$
declare
  f pg_temp.fx%rowtype;
  c uuid;
  v integer;
  r jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.email_change(pg_temp.vendor('ven_forged'), '000009401'));
  v := pg_temp.to_blocked(c);
  set local role service_role;
  insert into audit.evidence_events (chain_id, seq, event_id, dataset_id, case_id, type, actor_kind, actor_id, occurred_at,
                                     body_jcs, content_hash, prev_hash, hash)
    select f.chain_id, max(e.seq) + 1, gen_random_uuid(), f.dataset, c, 'release_co_approved', 'person', f.approver2, now(),
           format('{"body":{"reason":"forged"},"from_state":"blocked","to_state":"blocked","version":%s}', v),
           repeat('d', 64), repeat('e', 64), repeat('f', 64)
      from audit.evidence_events e where e.chain_id = f.chain_id;
  perform pg_temp.expect_error(format($q$ insert into app.case_event_receipts (idempotency_key, case_id, event_type, result)
            values ('forged', %L, 'release_co_approved', '{"to_state": "blocked"}') $q$, c), '42501');
  reset role;

  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), 'g1', %L) $q$,
            c, f.approver1, format('{"reason": "Ramp never changed.", "expected_version": %s}', v)), '23514', '%guard failed%');
  r := app.release_case(c, v, 'approve', 'Ramp never changed.');
  assert r ->> 'status' = 'awaiting_second_approver' and pg_temp.state_of(c) = 'blocked', r::text;
  perform pg_temp.sign_out();
end
$$;

rollback;
