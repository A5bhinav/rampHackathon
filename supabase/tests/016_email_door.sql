-- 016: stored inbound emails and the per-dataset email door switch (spec 0084).
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f        pg_temp.fx%rowtype;
  demo_ds  uuid;
  demo_acc uuid := gen_random_uuid();
  n        integer;
begin
  f := pg_temp.setup_fixture();                       -- a 'dev' dataset
  insert into app.datasets (kind, label, chain_id) values ('demo', 'test demo', f.chain_id) returning id into demo_ds;

  -- The door is off by default: no vendor is listed.
  assert (select email_door_vendors from app.datasets where id = f.dataset) = '{}'::uuid[], 'email_door_vendors defaults to empty';
  assert (select count(*) from information_schema.columns
           where table_schema = 'app' and table_name = 'datasets' and column_name = 'email_door_vendors'
             and is_nullable = 'NO') = 1, 'email_door_vendors is not null';

  -- Only the service role writes; it can make a row that has not been fetched yet (no raw_sha256).
  set local role service_role;
  insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments)
    values (f.dataset, 'em_1', 'ap@acme-fasteners.example', now(), false);
  insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments)
    values (demo_ds, 'em_1', 'ap@acme-fasteners.example', now(), false);   -- same id, other dataset: allowed
  update app.inbound_emails set raw_sha256 = repeat('a', 64), outcome = 'case_opened', reply = 'sent',
         red_flags = '{lookalike_domain}', last4 = '1234', processed_at = now()
   where dataset_id = f.dataset and resend_email_id = 'em_1';
  reset role;
  assert (select outcome from app.inbound_emails where dataset_id = demo_ds) = 'pending', 'outcome defaults to pending';
  assert (select reply from app.inbound_emails where dataset_id = demo_ds) = 'not_flagged', 'reply defaults to not_flagged';
  assert (select not flagged and tries = 0 and red_flags = '{}' from app.inbound_emails where dataset_id = demo_ds), 'defaults';

  -- Bad values and duplicates are refused.
  set local role service_role;
  perform pg_temp.expect_error(format($q$ insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments, outcome)
              values (%L, 'em_2', 'x@y.example', now(), false, 'nonsense') $q$, f.dataset), '23514');
  perform pg_temp.expect_error(format($q$ insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments, reply)
              values (%L, 'em_2', 'x@y.example', now(), false, 'nonsense') $q$, f.dataset), '23514');
  perform pg_temp.expect_error(format($q$ insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments, last4)
              values (%L, 'em_2', 'x@y.example', now(), false, '12345') $q$, f.dataset), '23514');
  perform pg_temp.expect_error(format($q$ insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments)
              values (%L, 'em_1', 'x@y.example', now(), false) $q$, f.dataset), '23505');
  reset role;

  -- A signed-in person reads; nobody but the service role writes.
  set local role authenticated;
  perform pg_temp.sign_in(f.clerk);
  select count(*) into n from app.inbound_emails;  assert n = 2, 'an authenticated person reads the rows';
  perform pg_temp.expect_error(format($q$ insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments)
              values (%L, 'em_3', 'x@y.example', now(), false) $q$, f.dataset), '42501');
  perform pg_temp.expect_error($q$ update app.inbound_emails set outcome = 'failed' $q$, '42501');
  perform pg_temp.expect_error($q$ delete from app.inbound_emails $q$, '42501');
  reset role;

  -- A demo account sees only the emails of demo datasets.
  insert into app.user_roles (user_id, role) values (demo_acc, 'approver');
  insert into app.demo_accounts (user_id) values (demo_acc);
  set local role authenticated;
  perform pg_temp.sign_in(demo_acc);
  select count(*) into n from app.inbound_emails where dataset_id = f.dataset;  assert n = 0, 'a demo account cannot see a non-demo dataset''s emails';
  select count(*) into n from app.inbound_emails where dataset_id = demo_ds;    assert n = 1, 'but sees a demo dataset''s emails';
  reset role;
end
$$;

rollback;
