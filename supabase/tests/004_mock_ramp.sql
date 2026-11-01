-- 004: the Ramp mock behaves like Ramp where it matters: holds clear payment dates, releases
-- do not restore them, bank changes send (or silently skip) webhooks, duplicates duplicate.
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  v_biz    uuid;
  v_user   uuid;
  v_vendor uuid;
  v_sub    uuid;
  v_acct0  uuid;
  v_acct1  uuid;
  v_hold   uuid;
  v_hold2  uuid;
begin
  insert into mock_ramp.businesses (dataset_id, name) values (gen_random_uuid(), 'Demo Co') returning id into v_biz;
  insert into mock_ramp.settings (business_id) values (v_biz);
  insert into mock_ramp.users (business_id, email, first_name, last_name) values (v_biz, 'ap@demo.co', 'Pat', 'Clerk') returning id into v_user;
  insert into mock_ramp.vendors (business_id, name) values (v_biz, 'Acme Fasteners LLC') returning id into v_vendor;
  insert into mock_ramp.vendor_accounts (business_id, vendor_id, routing_number, account_number) values (v_biz, v_vendor, '021000021', '000001234') returning id into v_acct0;
  insert into mock_ramp.bills (business_id, vendor_id, invoice_number, amount_cents, payment_date) values
    (v_biz, v_vendor, 'INV-1', 125000, current_date + 3),
    (v_biz, v_vendor, 'INV-2', 80000,  current_date + 10);
  insert into mock_ramp.bills (business_id, vendor_id, invoice_number, amount_cents, payment_date, status, paid_at) values
    (v_biz, v_vendor, 'INV-0', 5000, current_date - 30, 'PAID', now() - interval '30 days');
  insert into mock_ramp.webhook_subscriptions (business_id, url, secret) values (v_biz, 'http://core/webhooks/ramp', 'whsec_test') returning id into v_sub;

  -- A bank change by a known user: new account active, old inactive, audit event with the actor, one webhook.
  v_acct1 := mock_ramp.change_bank_account(v_vendor, '121000248', '000009876', v_user);
  assert (select is_active from mock_ramp.vendor_accounts where id = v_acct1), 'new account active';
  assert (select not is_active from mock_ramp.vendor_accounts where id = v_acct0), 'old account inactive';
  assert (select account_number_last4 = '9876' from mock_ramp.vendor_accounts_api where id = v_acct1), 'API view masks the number';
  assert (select count(*) = 1 from mock_ramp.audit_events where target_id = v_vendor and event_type = 'VENDOR_BANK_ACCOUNT_UPDATED' and actor_id = v_user), 'audit event with actor';
  assert (select count(*) = 1 from mock_ramp.webhook_deliveries where subscription_id = v_sub and event_type = 'vendors.updated'), 'one webhook';

  -- Chaos: duplicates send twice with the same event id; silent mode sends nothing.
  update mock_ramp.settings set duplicate_webhooks = true where business_id = v_biz;
  v_acct1 := mock_ramp.change_bank_account(v_vendor, '026009593', '000004444', null);
  assert (select count(*) = 3 from mock_ramp.webhook_deliveries where subscription_id = v_sub), 'duplicate delivered';
  assert (select count(distinct event_id) = 2 from mock_ramp.webhook_deliveries where subscription_id = v_sub), 'duplicate shares the event id';
  assert exists (select 1 from mock_ramp.audit_events where target_id = v_vendor and actor_id is null and payload ->> 'new_account_id' = v_acct1::text), 'vendor-supplied change has no actor';
  update mock_ramp.settings set duplicate_webhooks = false, silent_changes = true where business_id = v_biz;
  perform mock_ramp.change_bank_account(v_vendor, '026009593', '000003333', v_user);
  assert (select count(*) = 3 from mock_ramp.webhook_deliveries where subscription_id = v_sub), 'silent change sent no webhook';
  update mock_ramp.settings set silent_changes = false where business_id = v_biz;

  -- Hold: clears every unpaid bill's payment date, leaves the paid one alone, is idempotent.
  v_hold := mock_ramp.place_hold(v_vendor, 'bank change under verification', 'dialback');
  assert (select bool_and(is_on_hold and payment_date is null) from mock_ramp.bills where vendor_id = v_vendor and paid_at is null), 'unpaid bills held, dates cleared';
  assert (select not is_on_hold and payment_date is not null from mock_ramp.bills where invoice_number = 'INV-0'), 'paid bill untouched';
  v_hold2 := mock_ramp.place_hold(v_vendor);
  assert v_hold2 = v_hold, 'second hold is the same hold';
  assert (select count(*) = 1 from mock_ramp.vendor_holds where vendor_id = v_vendor and released_at is null), 'one active hold';

  -- Release: lifts the hold, bills are no longer on hold, payment dates stay cleared (Dialback restores them).
  assert mock_ramp.release_hold(v_vendor), 'released';
  assert (select count(*) = 0 from mock_ramp.vendor_holds where vendor_id = v_vendor and released_at is null), 'no active hold';
  assert (select bool_and(not is_on_hold and payment_date is null) from mock_ramp.bills where vendor_id = v_vendor and paid_at is null), 'dates not restored';
  assert not mock_ramp.release_hold(v_vendor), 'releasing twice is a no-op';

  -- One-shot failure switches: consumed once by the API layer, then clear.
  update mock_ramp.settings set fail_next_hold = true where business_id = v_biz;
  assert mock_ramp.consume_switch(v_biz, 'fail_next_hold'), 'first read fires';
  assert not mock_ramp.consume_switch(v_biz, 'fail_next_hold'), 'second read is clear';
  assert (select not fail_next_hold from mock_ramp.settings where business_id = v_biz), 'switch reset';
  perform pg_temp.expect_error(format($q$ select mock_ramp.consume_switch(%L, 'nonsense') $q$, v_biz));
  perform mock_ramp.place_hold(v_vendor);
end
$$;

rollback;
