-- 012: the ledger's wiring tables (spec 0024): the vendor's own domains and the change requests.
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f     pg_temp.fx%rowtype;
  v_doc uuid;
begin
  f := pg_temp.setup_fixture();
  set local role service_role;
  insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at, sender, thread_id, subject)
    values (f.dataset, f.vendor, 'email', repeat('7', 64), 'email', now(), 'ar@acme.example', 'thr', 'Updated banking details')
    returning id into v_doc;
  insert into ledger.vendor_domains (dataset_id, vendor_id, domain) values (f.dataset, f.vendor, 'acme.example');
  insert into ledger.change_requests (dataset_id, vendor_id, document_id) values (f.dataset, f.vendor, v_doc);
  assert (select recorded_at from ledger.change_requests where document_id = v_doc) is not null, 'recorded_at is stamped';

  -- A domain is a bare lowercase name; a request is a document of that vendor and dataset.
  perform pg_temp.expect_error(format($q$ insert into ledger.vendor_domains (dataset_id, vendor_id, domain) values (%L, %L, 'Acme.Example') $q$, f.dataset, f.vendor), '23514');
  perform pg_temp.expect_error(format($q$ insert into ledger.vendor_domains (dataset_id, vendor_id, domain) values (%L, %L, 'ar@acme.example') $q$, f.dataset, f.vendor), '23514');
  perform pg_temp.expect_error(format($q$ insert into ledger.vendor_domains (dataset_id, vendor_id, domain) values (%L, %L, 'acme.example') $q$, f.dataset, f.vendor), '23505');
  perform pg_temp.expect_error(format($q$ insert into ledger.change_requests (dataset_id, vendor_id, document_id) values (%L, %L, %L) $q$, f.dataset, gen_random_uuid(), v_doc), '23514', '%vendor%');

  -- Append-only, like the rest of the ledger.
  perform pg_temp.expect_error($q$ delete from ledger.change_requests $q$, '42501');
  perform pg_temp.expect_error($q$ update ledger.vendor_domains set domain = 'evil.example' $q$, '42501');

  set local role authenticated;
  perform pg_temp.sign_in(f.approver1);
  assert (select count(*) from ledger.vendor_domains where vendor_id = f.vendor) = 1, 'signed-in users read';
  perform pg_temp.expect_error(format($q$ insert into ledger.vendor_domains (dataset_id, vendor_id, domain) values (%L, %L, 'x.example') $q$, f.dataset, f.vendor), '42501');
  perform pg_temp.sign_out();
  reset role;
  perform pg_temp.expect_error($q$ delete from ledger.change_requests $q$, '42501', '%append-only%');
end
$$;

rollback;
