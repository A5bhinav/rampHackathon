-- 010: the Trust Ledger schema holds its own rules (spec 0019).
begin;
\i supabase/tests/_helpers.sql

do $$
declare
  f      pg_temp.fx%rowtype;
  r      jsonb;
  v_case uuid;
  v_mail uuid;
  v_att  uuid;
  v_ctr  uuid;
  v_obs  uuid;
  v_cp   uuid;
  v_sel  uuid := gen_random_uuid();
  v_doc  jsonb;
  v_fp   text := repeat('ab', 32);
begin
  f := pg_temp.setup_fixture();
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_1:detected', jsonb_build_object('change_event_id', f.change1));
  v_case := (r ->> 'case_id')::uuid;

  set local role service_role;

  -- Custody: an email, its attachment (same custody), and a stored contract.
  insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at, claimed_date, sender, thread_id, subject)
    values (f.dataset, f.vendor, 'email', repeat('1', 64), 'email', '2025-01-10T15:00:00Z', '2019-01-01', 'ar@acme.example', 'thr_1', 'Invoice 1001')
    returning id into v_mail;
  insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at, parent_document_id)
    values (f.dataset, f.vendor, 'invoice', repeat('2', 64), 'email', '2025-01-10T15:00:00Z', v_mail)
    returning id into v_att;
  perform pg_temp.expect_error(format($q$ insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at, parent_document_id)
    values (%L, %L, 'invoice', repeat('3', 64), 'vendor_portal', '2025-01-10T15:00:00Z', %L) $q$, f.dataset, f.vendor, v_mail), '23514', '%parent%');
  perform pg_temp.expect_error(format($q$ insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at, parent_document_id)
    values (%L, %L, 'invoice', repeat('3', 64), 'email', '2025-01-11T15:00:00Z', %L) $q$, f.dataset, f.vendor, v_mail), '23514', '%parent%');
  perform pg_temp.expect_error(format($q$ insert into ledger.documents (dataset_id, kind, sha256, custody_channel, received_at)
    values (%L, 'email', 'not-a-hash', 'email', now()) $q$, f.dataset), '23514');
  insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at)
    values (f.dataset, f.vendor, 'contract', repeat('4', 64), 'contract', '2024-03-01T12:00:00Z')
    returning id into v_ctr;
  -- Entity resolution may still be pending: a document with no vendor yet is allowed.
  insert into ledger.documents (dataset_id, kind, sha256, custody_channel, received_at)
    values (f.dataset, 'listing', repeat('5', 64), 'listing', '2024-05-01T12:00:00Z');

  -- Provenance comes from custody: the insert's source_type and seen_at are overwritten.
  insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, role, party, quote, visibility,
                                   source_type, seen_at, extractor_version)
    values (f.dataset, v_att, f.vendor, 'phone', '+14155550142', 'ar', 'vendor', 'AR: (415) 555-0142', 'visible',
            'contract', '2015-01-01T00:00:00Z', 'test-0')
    returning id into v_obs;
  assert (select source_type from ledger.observations where id = v_obs) = 'email', 'source_type comes from the document';
  assert (select seen_at from ledger.observations where id = v_obs) = '2025-01-10T15:00:00Z'::timestamptz, 'seen_at is our receive time';
  assert (select recorded_at from ledger.observations where id = v_obs) is not null, 'recorded_at is the database clock';
  perform pg_temp.expect_error(format($q$ insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, visibility, extractor_version)
    values (%L, %L, %L, 'phone', '415-555-0142', 'visible', 'test-0') $q$, f.dataset, v_ctr, f.vendor), '23514');

  -- ADR-8: a bank account is routing:last4:fingerprint, never a full number, and never quoted.
  insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, party, visibility, extractor_version)
    values (f.dataset, v_ctr, f.vendor, 'bank_account', '021000021:1234:' || v_fp, 'bank', 'visible', 'test-0');
  perform pg_temp.expect_error(format($q$ insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, visibility, extractor_version)
    values (%L, %L, %L, 'bank_account', '021000021:000001234567', 'visible', 'test-0') $q$, f.dataset, v_ctr, f.vendor), '23514');
  perform pg_temp.expect_error(format($q$ insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, quote, visibility, extractor_version)
    values (%L, %L, %L, 'bank_account', %L, 'Acct 000001234567', 'visible', 'test-0') $q$, f.dataset, v_ctr, f.vendor, '021000021:1234:' || v_fp), '23514');

  -- Append-only for everyone, the service role included.
  perform pg_temp.expect_error(format($q$ update ledger.observations set role = 'main' where id = %L $q$, v_obs), '42501');
  perform pg_temp.expect_error(format($q$ delete from ledger.observations where id = %L $q$, v_obs), '42501');
  perform pg_temp.expect_error(format($q$ update ledger.documents set received_at = now() where id = %L $q$, v_mail), '42501');

  -- Contact points are a summary the service role keeps up to date.
  insert into ledger.contact_points (dataset_id, vendor_id, e164, first_seen_at, source_types, status)
    values (f.dataset, f.vendor, '+14155550142', '2024-03-01T12:00:00Z', '{contract,email}', 'eligible')
    returning id into v_cp;
  update ledger.contact_points set status = 'excluded', exclusion_reason = 'test' where id = v_cp;
  perform pg_temp.expect_error(format($q$ insert into ledger.contact_points (dataset_id, vendor_id, e164, first_seen_at, status)
    values (%L, %L, '+14155550142', now(), 'eligible') $q$, f.dataset, f.vendor), '23505');
  perform pg_temp.expect_error(format($q$ insert into ledger.contact_points (dataset_id, vendor_id, e164, first_seen_at, status)
    values (%L, %L, '+14155550199', now(), 'excluded') $q$, f.dataset, f.vendor), '23514');

  -- A selection agrees with its own document.
  v_doc := jsonb_build_object('v', 1, 'number_selection_id', v_sel, 'case_id', v_case, 'dataset_id', f.dataset, 'vendor_id', f.vendor,
             'rule_version', 'ledger-select-v1', 'outcome', 'picked', 'contact_point_id', v_cp, 'e164', '+14155550142',
             'inputs_sha256', repeat('e', 64));
  perform pg_temp.expect_error(format($q$ insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, outcome, rule_version, inputs_sha256, selection)
    values (%L, %L, %L, %L, 'picked', 'ledger-select-v1', repeat('e', 64), %L) $q$, v_sel, f.dataset, v_case, f.vendor, v_doc), '23514');
  perform pg_temp.expect_error(format($q$ insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, rule_version, inputs_sha256, selection)
    values (%L, %L, %L, %L, %L, 'no_provable_number', 'ledger-select-v1', repeat('e', 64), %L) $q$, v_sel, f.dataset, v_case, f.vendor, v_cp, v_doc), '23514');
  perform pg_temp.expect_error(format($q$ insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, rule_version, inputs_sha256, selection)
    values (%L, %L, %L, %L, %L, 'picked', 'ledger-select-v1', repeat('f', 64), %L) $q$, v_sel, f.dataset, v_case, f.vendor, v_cp, v_doc), '23514');
  insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, rule_version, inputs_sha256, selection)
    values (v_sel, f.dataset, v_case, f.vendor, v_cp, 'picked', 'ledger-select-v1', repeat('e', 64), v_doc);
  perform pg_temp.expect_error(format($q$ update ledger.number_selections set outcome = 'no_provable_number' where id = %L $q$, v_sel), '42501');
  perform pg_temp.expect_error(format($q$ delete from ledger.number_selections where id = %L $q$, v_sel), '42501');

  -- A signed-in user reads and writes nothing; anon has no way in.
  set local role authenticated;
  perform pg_temp.sign_in(f.approver1);
  assert (select count(*) from ledger.observations where vendor_id = f.vendor) = 2, 'authenticated can read observations';
  assert (select count(*) from ledger.number_selections where id = v_sel) = 1, 'authenticated can read selections';
  perform pg_temp.expect_error(format($q$ insert into ledger.documents (dataset_id, kind, sha256, custody_channel, received_at)
    values (%L, 'email', repeat('9', 64), 'email', now()) $q$, f.dataset), '42501');
  perform pg_temp.expect_error(format($q$ update ledger.contact_points set status = 'eligible' where id = %L $q$, v_cp), '42501');
  perform pg_temp.sign_out();
  set local role anon;
  perform pg_temp.expect_error($q$ select count(*) from ledger.observations $q$, '42501');
  reset role;

  -- The triggers hold even for the table owner, whose grants don't stop it.
  perform pg_temp.expect_error(format($q$ update ledger.observations set role = 'main' where id = %L $q$, v_obs), '42501', '%append-only%');
  perform pg_temp.expect_error(format($q$ delete from ledger.number_selections where id = %L $q$, v_sel), '42501', '%append-only%');
  perform pg_temp.expect_error(format($q$ delete from ledger.documents where id = %L $q$, v_ctr), '42501', '%append-only%');
end
$$;


-- Review fixes: identities are fixed, a selection agrees with its case and contact point, and
-- provenance and clocks can't be supplied by the insert.
do $$
declare
  f      pg_temp.fx%rowtype;
  g      pg_temp.fx%rowtype;
  r      jsonb;
  v_case uuid;
  v_doc  uuid;
  v_obs  uuid;
  v_cp   uuid;
  v_cp_g uuid;
  v_sel  uuid := gen_random_uuid();
  v_none uuid := gen_random_uuid();
  base   jsonb;
begin
  f := pg_temp.setup_fixture();
  g := pg_temp.setup_fixture();  -- another dataset and vendor
  r := app.apply_case_event(null, 'change_detected', pg_temp.sys(), 'evt_1:detected:review', jsonb_build_object('change_event_id', f.change1));
  v_case := (r ->> 'case_id')::uuid;

  set local role service_role;
  insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at)
    values (f.dataset, f.vendor, 'contract', repeat('6', 64), 'contract', '2024-01-01T00:00:00Z') returning id into v_doc;

  -- recorded_at is the database's clock, whatever the insert says.
  insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, visibility, extractor_version, recorded_at)
    values (f.dataset, v_doc, f.vendor, 'phone', '+14155550142', 'visible', 'test-0', '2000-01-01T00:00:00Z') returning id into v_obs;
  assert (select recorded_at from ledger.observations where id = v_obs) > '2020-01-01'::timestamptz, 'recorded_at is not taken from the insert';
  -- An observation can't sit in another dataset than its document.
  perform pg_temp.expect_error(format($q$ insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, visibility, extractor_version)
    values (%L, %L, %L, 'phone', '+14155550142', 'visible', 'test-0') $q$, g.dataset, v_doc, g.vendor), '23514', '%dataset%');

  insert into ledger.contact_points (dataset_id, vendor_id, e164, first_seen_at, status)
    values (f.dataset, f.vendor, '+14155550142', '2024-01-01T00:00:00Z', 'eligible') returning id into v_cp;
  insert into ledger.contact_points (dataset_id, vendor_id, e164, first_seen_at, status)
    values (g.dataset, g.vendor, '+14155550142', '2024-01-01T00:00:00Z', 'eligible') returning id into v_cp_g;
  -- A contact point's number can't be rewritten after a selection names it.
  perform pg_temp.expect_error(format($q$ update ledger.contact_points set e164 = '+14155550169' where id = %L $q$, v_cp), '42501');
  perform pg_temp.expect_error(format($q$ update ledger.contact_points set vendor_id = %L where id = %L $q$, g.vendor, v_cp), '42501');
  update ledger.contact_points set status = 'excluded', exclusion_reason = 'review' where id = v_cp;
  update ledger.contact_points set status = 'eligible', exclusion_reason = null where id = v_cp;

  base := jsonb_build_object('v', 1, 'case_id', v_case, 'dataset_id', f.dataset, 'vendor_id', f.vendor,
            'rule_version', 'ledger-select-v1', 'inputs_sha256', repeat('e', 64));
  -- Another vendor's contact point, or a number that isn't the contact point's, is refused.
  perform pg_temp.expect_error(format($q$ insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, rule_version, inputs_sha256, selection)
    values (%L, %L, %L, %L, %L, 'picked', 'ledger-select-v1', repeat('e', 64), %L) $q$, v_sel, f.dataset, v_case, f.vendor, v_cp_g,
    base || jsonb_build_object('number_selection_id', v_sel, 'outcome', 'picked', 'contact_point_id', v_cp_g, 'e164', '+14155550142')), '23514', '%contact point%');
  perform pg_temp.expect_error(format($q$ insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, rule_version, inputs_sha256, selection)
    values (%L, %L, %L, %L, %L, 'picked', 'ledger-select-v1', repeat('e', 64), %L) $q$, v_sel, f.dataset, v_case, f.vendor, v_cp,
    base || jsonb_build_object('number_selection_id', v_sel, 'outcome', 'picked', 'contact_point_id', v_cp, 'e164', '+14155550169')), '23514', '%contact point%');
  -- A selection for another vendor than its case's is refused.
  perform pg_temp.expect_error(format($q$ insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, outcome, rule_version, inputs_sha256, selection)
    values (%L, %L, %L, %L, 'no_provable_number', 'ledger-select-v1', repeat('e', 64), %L) $q$, v_none, g.dataset, v_case, g.vendor,
    base || jsonb_build_object('number_selection_id', v_none, 'outcome', 'no_provable_number', 'dataset_id', g.dataset, 'vendor_id', g.vendor)), '23514', '%case%');
  -- Each field of the document must match its column.
  perform pg_temp.expect_error(format($q$ insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, outcome, rule_version, inputs_sha256, selection)
    values (%L, %L, %L, %L, 'no_provable_number', 'ledger-select-v2', repeat('e', 64), %L) $q$, v_none, f.dataset, v_case, f.vendor,
    base || jsonb_build_object('number_selection_id', v_none, 'outcome', 'no_provable_number')), '23514', '%matches_its_document%');
  -- A valid refusal is stored.
  insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, outcome, rule_version, inputs_sha256, selection)
    values (v_none, f.dataset, v_case, f.vendor, 'no_provable_number', 'ledger-select-v1', repeat('e', 64),
            base || jsonb_build_object('number_selection_id', v_none, 'outcome', 'no_provable_number', 'reason', 'none'));

  set local role authenticated;
  perform pg_temp.sign_in(f.approver1);
  perform pg_temp.expect_error(format($q$ insert into ledger.observations (dataset_id, document_id, kind, value_normalized, visibility, extractor_version)
    values (%L, %L, 'phone', '+14155550142', 'visible', 'x') $q$, f.dataset, v_doc), '42501');
  perform pg_temp.expect_error(format($q$ delete from ledger.number_selections where id = %L $q$, v_none), '42501');
  perform pg_temp.sign_out();
  reset role;

  -- The owner's grants don't stop it, but the trigger keeps a contact point's number fixed.
  perform pg_temp.expect_error(format($q$ update ledger.contact_points set e164 = '+14155550169' where id = %L $q$, v_cp), '42501', '%fixed%');
  -- Truncate is refused even for the owner.
  perform pg_temp.expect_error($q$ truncate ledger.observations cascade $q$, '42501', '%append-only%');
  perform pg_temp.expect_error($q$ truncate ledger.number_selections $q$, '42501', '%append-only%');
end
$$;


-- Spec 0092: text in a PDF that leaves no ink on the page, and (Part B) text whose digits couldn't be
-- confirmed. Both are hidden sightings: stored, never quoted.
do $$
declare
  f     pg_temp.fx%rowtype;
  v_doc uuid;
begin
  f := pg_temp.setup_fixture();
  set local role service_role;
  insert into ledger.documents (dataset_id, vendor_id, kind, sha256, custody_channel, received_at)
    values (f.dataset, f.vendor, 'contract', repeat('7', 64), 'contract', '2024-02-01T00:00:00Z') returning id into v_doc;
  insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, visibility, extractor_version)
    values (f.dataset, v_doc, f.vendor, 'phone', '+14155550169', 'no_ink', 'pdf-1/test-0'),
           (f.dataset, v_doc, f.vendor, 'phone', '+14155550170', 'unverified', 'pdf-1/test-0');
  assert (select array_agg(visibility::text order by value_normalized) from ledger.observations
           where document_id = v_doc and quote is null) = array['no_ink', 'unverified'],
         'no_ink and unverified sightings are stored, unquoted';
  reset role;
end
$$;

rollback;
