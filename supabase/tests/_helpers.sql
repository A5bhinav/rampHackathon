-- Shared helpers for the SQL tests. Each test file includes this with \i and runs inside
-- one transaction that is rolled back, so tests leave nothing behind.

create function pg_temp.sign_in(p_user uuid) returns void
language sql as $$
  select set_config('request.jwt.claims', json_build_object('sub', p_user, 'role', 'authenticated')::text, true)
$$;

create function pg_temp.sign_out() returns void
language sql as $$
  select set_config('request.jwt.claims', '', true)
$$;

-- Run p_sql and insist that it fails, optionally with a given SQLSTATE or message pattern.
create function pg_temp.expect_error(p_sql text, p_state text default null, p_like text default null) returns void
language plpgsql as $$
begin
  execute p_sql;
  raise exception using errcode = 'TST01', message = format('expected an error but this succeeded: %s', p_sql);
exception
  when others then
    if sqlstate = 'TST01' then
      raise;
    end if;
    if p_state is not null and sqlstate <> p_state then
      raise exception 'expected SQLSTATE % but got % (%) for: %', p_state, sqlstate, sqlerrm, p_sql;
    end if;
    if p_like is not null and sqlerrm not like p_like then
      raise exception 'expected a message like % but got: %', p_like, sqlerrm;
    end if;
end
$$;

create function pg_temp.sys() returns jsonb
language sql immutable as $$ select '{"kind": "system", "id": null}'::jsonb $$;

create function pg_temp.person(p_user uuid) returns jsonb
language sql immutable as $$ select jsonb_build_object('kind', 'person', 'id', p_user) $$;

-- A dataset with its own chain, a vendor with two accounts, and a change event.
create table pg_temp.fx (
  clerk     uuid default gen_random_uuid(),
  approver1 uuid default gen_random_uuid(),
  approver2 uuid default gen_random_uuid(),
  approver3 uuid default gen_random_uuid(),
  chain_id  uuid,
  dataset   uuid,
  vendor    uuid,
  acct_old  uuid,
  acct_new  uuid,
  change1   uuid
);

create function pg_temp.fingerprint(p_routing text, p_account text) returns text
language sql immutable as $$
  select encode(hmac(p_routing || p_account, 'test-key', 'sha256'), 'hex')
$$;

create function pg_temp.setup_fixture() returns pg_temp.fx
language plpgsql as $$
declare
  f pg_temp.fx%rowtype;
begin
  insert into pg_temp.fx default values returning * into f;
  insert into audit.chains (label) values ('test') returning id into f.chain_id;
  insert into app.datasets (kind, label, chain_id) values ('dev', 'test', f.chain_id) returning id into f.dataset;
  insert into app.user_roles (user_id, role) values
    (f.clerk, 'clerk'), (f.approver1, 'approver'), (f.approver2, 'approver'), (f.approver3, 'approver');
  insert into app.vendors (dataset_id, ramp_vendor_id, legal_name)
    values (f.dataset, 'ven_1', 'Acme Fasteners LLC') returning id into f.vendor;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint, first_seen_at)
    values (f.dataset, f.vendor, '021000021', '1234', pg_temp.fingerprint('021000021', '000001234'), now() - interval '400 days')
    returning id into f.acct_old;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint)
    values (f.dataset, f.vendor, '121000248', '9876', pg_temp.fingerprint('121000248', '000009876'))
    returning id into f.acct_new;
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id, changed_by)
    values (f.dataset, f.vendor, f.acct_old, f.acct_new, 'webhook', 'evt_1', f.clerk)
    returning id into f.change1;
  update pg_temp.fx set chain_id = f.chain_id, dataset = f.dataset, vendor = f.vendor,
         acct_old = f.acct_old, acct_new = f.acct_new, change1 = f.change1;
  return f;
end
$$;

create function pg_temp.new_change(p_source text, p_changed_by uuid) returns uuid
language plpgsql as $$
declare
  f pg_temp.fx%rowtype;
  v_acct uuid;
  v_id uuid;
begin
  select * into f from pg_temp.fx limit 1;
  insert into app.bank_accounts (dataset_id, vendor_id, routing_number, account_last4, account_fingerprint)
    values (f.dataset, f.vendor, '026009593', right(p_source, 4), pg_temp.fingerprint('026009593', p_source))
    returning id into v_acct;
  insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via, source_event_id, changed_by)
    values (f.dataset, f.vendor, f.acct_new, v_acct, 'poll', p_source, p_changed_by)
    returning id into v_id;
  return v_id;
end
$$;

create function pg_temp.state_of(p_case uuid) returns text
language sql as $$ select state::text from app.cases where id = p_case $$;

-- Start the case's current call attempt the way the core does after the voice service
-- answers 202 (spec 0002, D6), and return the call id to report on.
create function pg_temp.start_call(p_case uuid) returns uuid
language plpgsql as $$
declare
  c      app.cases%rowtype;
  v_call uuid := gen_random_uuid();
begin
  select * into c from app.cases where id = p_case;
  perform app.apply_case_event(p_case, 'call_started', pg_temp.sys(),
            format('%s:call:v%s:%s:started', p_case, c.version, c.call_attempts),
            jsonb_build_object('call_id', v_call, 'attempt', c.call_attempts));
  return v_call;
end
$$;
