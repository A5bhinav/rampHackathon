-- 001: the evidence chain canonicalizes, links, refuses edits, and points at a tampered row.
begin;
\i supabase/tests/_helpers.sql

create temp table ctx (chain_id uuid);

do $$
declare
  v_chain uuid;
  v_env   jsonb;
  r       record;
  v_jcs   text;
begin
  insert into audit.chains (label) values ('test-001') returning id into v_chain;
  insert into ctx values (v_chain);

  -- RFC 8785: keys sorted, strings escaped like JCS, integers kept, arrays ordered, nesting preserved
  v_jcs := audit.jcs('{"b": 1, "a": {"z": [true, null, "x\"y"], "y": "é", "t": "a\tb"}, "c": -5, "A": false}'::jsonb);
  assert v_jcs = '{"A":false,"a":{"t":"a\tb","y":"é","z":[true,null,"x\"y"]},"b":1,"c":-5}', 'jcs output: ' || v_jcs;
  assert audit.jcs('{}'::jsonb) = '{}' and audit.jcs('[]'::jsonb) = '[]', 'empty containers';

  -- Floats are refused: Postgres and Python would print them differently.
  perform pg_temp.expect_error($q$ select audit.jcs('{"amount": 1.5}'::jsonb) $q$, null, 'audit.jcs: only integers%');

  -- Three events link from the genesis hash.
  for i in 1..3 loop
    v_env := jsonb_build_object(
      'v', 1, 'chain_id', v_chain, 'event_id', gen_random_uuid(), 'type', 'test',
      'actor', jsonb_build_object('kind', 'system', 'id', null),
      'occurred_at', app.rfc3339(clock_timestamp()), 'body', jsonb_build_object('i', i));
    select * into r from audit.append_evidence(v_chain, v_env);
    assert r.seq = i, 'seq should be ' || i;
    assert r.content_hash = audit.content_hash(audit.jcs(v_env)), 'content hash';
  end loop;
  assert (select prev_hash from audit.evidence_events where chain_id = v_chain and seq = 1) = audit.genesis_hash(), 'genesis';
  assert (select e2.prev_hash = e1.hash
            from audit.evidence_events e1 join audit.evidence_events e2 on e2.chain_id = e1.chain_id and e2.seq = e1.seq + 1
           where e1.chain_id = v_chain and e1.seq = 1), 'linkage';
  assert (select e.hash = audit.chain_hash(e.prev_hash, e.content_hash) from audit.evidence_events e where e.chain_id = v_chain and e.seq = 3), 'hash formula';

  select * into r from audit.verify_chain(v_chain);
  assert r.ok and r.checked = 3, 'verify: ' || r.detail;
  select * into r from audit.chain_tip(v_chain);
  assert r.seq = 3, 'tip';

  -- Envelope rules.
  perform pg_temp.expect_error(format($q$ select audit.append_evidence(%L, '{"v":1,"chain_id":"%s","event_id":"%s","type":"t","actor":{"kind":"system","id":null},"occurred_at":"2026-11-01T00:00:00.000000Z","hash":"x"}') $q$,
                                      v_chain, v_chain, gen_random_uuid()), null, '%must not carry chain fields%');
  perform pg_temp.expect_error(format($q$ select audit.append_evidence(%L, '{"v":1,"chain_id":"%s","event_id":"%s","type":"t","actor":{"kind":"system","id":null},"occurred_at":"2026-11-01T00:00:00.000000Z"}') $q$,
                                      v_chain, gen_random_uuid(), gen_random_uuid()), null, '%does not match chain%');
  perform pg_temp.expect_error(format($q$ select audit.append_evidence(%L, '{"v":1,"chain_id":"%s","event_id":"%s","type":"t","actor":{"kind":"system","id":null},"occurred_at":"2026-11-01T00:00:00.000000Z"}', repeat('0', 64)) $q$,
                                      v_chain, v_chain, gen_random_uuid()), null, '%content hash mismatch%');
end
$$;

-- Nobody updates or deletes evidence: not the worker, not a signed-in user, not even the owner.
do $$
begin
  set local role service_role;
  perform pg_temp.expect_error($q$ update audit.evidence_events set type = 'edited' $q$, '42501');
  perform pg_temp.expect_error($q$ delete from audit.evidence_events $q$, '42501');
  set local role authenticated;
  perform pg_temp.expect_error($q$ update audit.evidence_events set type = 'edited' $q$, '42501');
  perform pg_temp.expect_error($q$ insert into audit.evidence_events select * from audit.evidence_events limit 0 $q$, '42501');
  reset role;
  perform pg_temp.expect_error($q$ update audit.evidence_events set type = 'edited' $q$, '42501');
  perform pg_temp.expect_error($q$ delete from audit.evidence_events $q$, '42501');
  perform pg_temp.expect_error($q$ truncate audit.evidence_events $q$, '42501');
end
$$;

-- A row edited by hand (owner, triggers disabled) makes the verify job point straight at it.
alter table audit.evidence_events disable trigger evidence_events_no_update;
update audit.evidence_events e set body_jcs = replace(e.body_jcs, '"i":2', '"i":7')
  from ctx where e.chain_id = ctx.chain_id and e.seq = 2;
alter table audit.evidence_events enable trigger evidence_events_no_update;

do $$
declare
  r record;
begin
  select v.* into r from ctx, lateral audit.verify_chain(ctx.chain_id) v;
  assert not r.ok, 'verify should fail after tampering';
  assert r.first_bad_seq = 2, 'first bad seq should be 2, got ' || coalesce(r.first_bad_seq::text, 'null');
  assert r.detail like 'content_hash does not match%', r.detail;
  assert r.checked = 1, 'checked rows before the break';
end
$$;

rollback;
