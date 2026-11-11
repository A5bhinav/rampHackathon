-- 018: the approvals refuse a case whose current change came by email when the case is not
-- email only (spec 0090; final review finding C1).
--
-- A case verifies its current change. The email door never adds an email's change to a case
-- that has a change which did not come by email (spec 0087), but if a bug or another writer ever
-- did, the call would verify the email's account while Ramp pays the clerk's. So first_approver
-- and four_eyes also refuse when the current change came by email and app.case_email_only is
-- false. Everything else about releasing stays as it was; the controls below must still release.
begin;
\i supabase/tests/_helpers.sql

-- The same fixtures as 017: a vendor with one account on file, an email change recorded the way
-- the door does it, a Ramp change recorded the way detection does it.
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

create function pg_temp.ramp_change(p_vendor uuid, p_source text, p_by uuid) returns uuid
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
    values (f.dataset, p_vendor, v_old, v_acct, 'webhook', 'ramp-account:' || p_source, p_by, clock_timestamp())
    returning id into v_id;
  return v_id;
end
$$;

create function pg_temp.detect(p_change uuid) returns uuid
language sql as $$
  select (app.apply_case_event(null, 'change_detected', pg_temp.sys(), p_change || ':detected',
            jsonb_build_object('change_event_id', p_change)) ->> 'case_id')::uuid
$$;

-- Hold, number, call and a Verified decision: the case waits in awaiting_release. Returns the version.
create function pg_temp.to_awaiting_release(p_case uuid) returns integer
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
            jsonb_build_object('outcome', 'verified', 'inputs_hash', repeat('c', 64), 'engine_version', 'test'));
  assert pg_temp.state_of(p_case) = 'awaiting_release', format('case %s is %s, not awaiting_release', p_case, pg_temp.state_of(p_case));
  return (select version from app.cases where id = p_case);
end
$$;

-- Every way an approver can try to release a case awaiting release: release_case (first and second
-- approver), and straight through apply_case_event with each approval event. Each must be refused,
-- and nothing may move: no approval row, no release_hold command, the case still awaiting release.
-- release_case is the call that the new checks alone refuse (first_approver with an unknown changer,
-- four_eyes with a known one); the direct event that does not match the changer kind is refused by
-- its guard's older rules too. Block 4b reaches four_eyes with an unknown changer.
create function pg_temp.approvals_refused(p_case uuid, p_version integer, p_label text) returns void
language plpgsql as $$
declare
  f    pg_temp.fx%rowtype;
  body text := format('{"reason": "Verified on the proven number.", "expected_version": %s}', p_version);
begin
  select * into f from pg_temp.fx limit 1;
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Verified on the proven number.') $q$,
            p_case, p_version), '23514', '%guard failed%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_co_approved', pg_temp.person(%L), %L, %L) $q$,
            p_case, f.approver1, p_label || ':co', body), '23514', '%guard failed%');
  perform pg_temp.expect_error(format($q$ select app.apply_case_event(%L, 'release_approved', pg_temp.person(%L), %L, %L) $q$,
            p_case, f.approver1, p_label || ':ap', body), '23514', '%guard failed%');
  perform pg_temp.sign_in(f.approver2);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Verified on the proven number.') $q$,
            p_case, p_version), '23514', '%guard failed%');
  perform pg_temp.sign_out();
  assert pg_temp.state_of(p_case) = 'awaiting_release', p_label || ': the case moved';
  assert not exists (select 1 from app.releases where case_id = p_case), p_label || ': an approval was recorded';
  assert not exists (select 1 from app.outbox where case_id = p_case and command = 'release_hold'), p_label || ': a release was queued';
end
$$;

-- Two different approvers release: the controls must still do this exactly as before.
create function pg_temp.two_approvers_release(p_case uuid, p_version integer, p_label text) returns void
language plpgsql as $$
declare
  f pg_temp.fx%rowtype;
  r jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(p_case, p_version, 'approve', 'Verified on the proven number.');
  assert r ->> 'status' = 'awaiting_second_approver', p_label || ': ' || r::text;
  perform pg_temp.sign_in(f.approver2);
  r := app.release_case(p_case, p_version, 'approve', 'Checked too.');
  assert r ->> 'status' = 'applied' and r ->> 'to_state' = 'releasing', p_label || ': ' || r::text;
  perform pg_temp.sign_out();
  assert (select count(*) from app.outbox where case_id = p_case and command = 'release_hold') = 1, p_label || ': one release_hold';
end
$$;

-- 1. A Ramp case that an email's change joined (the bug C1 found, written straight to the database):
--    the current change is the email's, Ramp holds the clerk's account. No approval releases it.
do $$
declare
  f  pg_temp.fx%rowtype;
  vr uuid;
  c  uuid;
  v  integer;
begin
  f := pg_temp.setup_fixture();
  vr := pg_temp.vendor('ven_ramp_then_email');
  c := pg_temp.detect(pg_temp.ramp_change(vr, '000003003', f.clerk));
  assert pg_temp.detect(pg_temp.email_change(vr, '000007777')) = c, 'the email joins the Ramp case (as the old door did)';
  assert (select ce.detected_via = 'email' from app.cases x join app.bank_change_events ce on ce.id = x.change_event_id where x.id = c),
    'the current change is the email''s';
  v := pg_temp.to_awaiting_release(c);
  assert (select has_unknown_changer from app.cases where id = c), 'the email has no known changer: two approvers';
  perform pg_temp.approvals_refused(c, v, 'ramp then email');
end
$$;

-- 2. The same with a known changer on both changes, so release_case goes straight to
--    release_approved: four_eyes refuses on its own, not only first_approver.
do $$
declare
  f  pg_temp.fx%rowtype;
  vk uuid;
  c  uuid;
  v  integer;
begin
  select * into f from pg_temp.fx limit 1;
  vk := pg_temp.vendor('ven_known_changer');
  c := pg_temp.detect(pg_temp.ramp_change(vk, '000003103', f.clerk));
  assert pg_temp.detect(pg_temp.email_change(vk, '000007877', f.clerk)) = c, 'the email joins';
  v := pg_temp.to_awaiting_release(c);
  assert not (select has_unknown_changer from app.cases where id = c), 'both changes have a known changer: one approver';
  perform pg_temp.approvals_refused(c, v, 'known changer');
end
$$;

-- 3. The other order: an email case, a Ramp change joins it, then another email joins.
do $$
declare
  f  pg_temp.fx%rowtype;
  vm uuid;
  c  uuid;
  v  integer;
begin
  select * into f from pg_temp.fx limit 1;
  vm := pg_temp.vendor('ven_email_ramp_email');
  c := pg_temp.detect(pg_temp.email_change(vm, '000008101'));
  assert pg_temp.detect(pg_temp.ramp_change(vm, '000008102', f.clerk)) = c, 'Ramp joins the email case';
  assert pg_temp.detect(pg_temp.email_change(vm, '000008103')) = c, 'a second email joins';
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.approvals_refused(c, v, 'email, Ramp, email');
end
$$;

-- 4. Not email only for another reason (rule C): Ramp's account on file is no longer the one the
--    email was compared against, with no Ramp change recorded. Refused too.
do $$
declare
  f  pg_temp.fx%rowtype;
  vc uuid;
  c  uuid;
  v  integer;
begin
  select * into f from pg_temp.fx limit 1;
  vc := pg_temp.vendor('ven_moved_on_file');
  c := pg_temp.detect(pg_temp.email_change(vc, '000008301'));
  v := pg_temp.to_awaiting_release(c);
  update app.bank_accounts set is_current = false where vendor_id = vc and is_current;
  insert into app.bank_accounts (dataset_id, vendor_id, ramp_account_id, routing_number, account_last4, account_fingerprint)
    values (f.dataset, vc, 'ra_moved', '121000248', '8302', pg_temp.fingerprint('121000248', '000008302'));
  assert not (select app.case_email_only(x) from app.cases x where x.id = c), 'the account on file moved: not email only';
  perform pg_temp.approvals_refused(c, v, 'account on file moved');
end
$$;

-- 4b. The account on file moves between the two approvals (no new version): the second approval
--     is refused. Here a first approval exists, so four_eyes's new check is the only refusal.
do $$
declare
  f  pg_temp.fx%rowtype;
  vd uuid;
  c  uuid;
  v  integer;
  r  jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  vd := pg_temp.vendor('ven_moves_between_approvals');
  c := pg_temp.detect(pg_temp.email_change(vd, '000008351'));
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Verified on the proven number.');
  assert r ->> 'status' = 'awaiting_second_approver', r::text;
  update app.bank_accounts set is_current = false where vendor_id = vd and is_current;
  insert into app.bank_accounts (dataset_id, vendor_id, ramp_account_id, routing_number, account_last4, account_fingerprint)
    values (f.dataset, vd, 'ra_moved_between', '121000248', '8352', pg_temp.fingerprint('121000248', '000008352'));
  perform pg_temp.sign_in(f.approver2);
  perform pg_temp.expect_error(format($q$ select app.release_case(%L, %s, 'approve', 'Checked too.') $q$, c, v), '23514', '%guard failed%');
  perform pg_temp.sign_out();
  assert pg_temp.state_of(c) = 'awaiting_release' and (select count(*) from app.releases where case_id = c) = 1, 'only the first approval';
  assert not exists (select 1 from app.outbox where case_id = c and command = 'release_hold'), 'no release queued';
end
$$;

-- 5. Not email only because the vendor had no account on file when the email arrived
--    (old_account_id null, rule A). The approvals refuse, so the hold stays on: fail closed, as spec
--    0090's open question 1 already does for the dismiss. Lifting that hold would pay nothing anyway.
do $$
declare
  f  pg_temp.fx%rowtype;
  vn uuid;
  c  uuid;
  v  integer;
begin
  select * into f from pg_temp.fx limit 1;
  insert into app.vendors (dataset_id, ramp_vendor_id, legal_name) values (f.dataset, 'ven_no_account', 'Vendor no account')
    returning id into vn;
  c := pg_temp.detect(pg_temp.email_change(vn, '000008401'));
  assert (select ce.old_account_id is null from app.cases x join app.bank_change_events ce on ce.id = x.change_event_id where x.id = c),
    'no account on file';
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.approvals_refused(c, v, 'no account on file');
end
$$;

-- Controls: each could fail if the guard refused too much.

-- 6. An email-only case still releases with two approvers, exactly as before.
do $$
declare
  c uuid;
  v integer;
begin
  c := pg_temp.detect(pg_temp.email_change(pg_temp.vendor('ven_email_only'), '000008501'));
  v := pg_temp.to_awaiting_release(c);
  assert (select app.case_email_only(x) from app.cases x where x.id = c), 'email only';
  perform pg_temp.two_approvers_release(c, v, 'email only');
end
$$;

-- 7. An email case that a Ramp change joined, with the Ramp change current: the call verified
--    Ramp's account, so the release goes ahead (two approvers: the email had no known changer).
do $$
declare
  f  pg_temp.fx%rowtype;
  vj uuid;
  c  uuid;
  v  integer;
begin
  select * into f from pg_temp.fx limit 1;
  vj := pg_temp.vendor('ven_email_then_ramp');
  c := pg_temp.detect(pg_temp.email_change(vj, '000008601'));
  assert pg_temp.detect(pg_temp.ramp_change(vj, '000008602', f.clerk)) = c, 'Ramp joins';
  assert (select ce.detected_via = 'webhook' from app.cases x join app.bank_change_events ce on ce.id = x.change_event_id where x.id = c),
    'the current change is Ramp''s';
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.two_approvers_release(c, v, 'email then Ramp');
end
$$;

-- 8. A Ramp case with a known changer: one approver, not the changer, releases it as before.
do $$
declare
  f pg_temp.fx%rowtype;
  c uuid;
  v integer;
  r jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  c := pg_temp.detect(pg_temp.ramp_change(pg_temp.vendor('ven_ramp_only'), '000008701', f.clerk));
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'approve', 'Verified on the proven number.');
  assert r ->> 'status' = 'applied' and r ->> 'to_state' = 'releasing', r::text;
  perform pg_temp.sign_out();
end
$$;

-- 9. Reject is untouched: a mixed case can still be rejected (the hold stays on).
do $$
declare
  f  pg_temp.fx%rowtype;
  vx uuid;
  c  uuid;
  v  integer;
  r  jsonb;
begin
  select * into f from pg_temp.fx limit 1;
  vx := pg_temp.vendor('ven_reject_mixed');
  c := pg_temp.detect(pg_temp.ramp_change(vx, '000008801', f.clerk));
  perform pg_temp.detect(pg_temp.email_change(vx, '000008802'));
  v := pg_temp.to_awaiting_release(c);
  perform pg_temp.sign_in(f.approver1);
  r := app.release_case(c, v, 'reject', 'Two different accounts were asked for.');
  assert r ->> 'to_state' = 'blocked', r::text;
  perform pg_temp.sign_out();
end
$$;

-- 10. The outcome check takes the door's new value and still refuses an unknown one.
do $$
declare
  f pg_temp.fx%rowtype;
begin
  select * into f from pg_temp.fx limit 1;
  insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments, outcome)
    values (f.dataset, 'em_ramp_case_open', 'a@b.example', now(), false, 'ramp_case_open');
  perform pg_temp.expect_error(format($q$ insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments, outcome)
            values (%L, 'em_bad', 'a@b.example', now(), false, 'joined_ramp_case') $q$, f.dataset), '23514');
end
$$;

rollback;
