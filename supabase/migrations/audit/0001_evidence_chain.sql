-- audit: the append-only, hash-chained evidence log. Owner: Abhi.
--
-- Enforces R6 (every state change lands in an append-only, hash-chained log) and ADR-7.
-- The chain is tamper-evident, not tamper-proof: a table owner can still rewrite it, which
-- is why the tip hash is committed to GitHub with every scorecard as an outside witness.
--
-- Hash contract, shared with Python (packages/contracts/vectors/evidence-chain.json
-- holds golden vectors that both sides must reproduce):
--
--   body_jcs      RFC 8785 canonical JSON of the envelope      audit.jcs(envelope)
--   content_hash  hex(sha256(utf8(body_jcs)))                   audit.content_hash(body_jcs)
--   hash          hex(sha256(utf8(prev_hash || content_hash)))  audit.chain_hash(prev, content)
--   genesis       prev_hash of the first event is 64 zeros      audit.genesis_hash()
--
-- Envelope rule: numbers in an envelope must be integers. Amounts travel as integer cents
-- or as strings, so Postgres and Python never disagree on how a float prints.

create table audit.chains (
  id         uuid primary key default gen_random_uuid(),
  label      text not null,
  created_at timestamptz not null default now()
);

create table audit.evidence_events (
  chain_id     uuid   not null references audit.chains (id),
  seq          bigint not null,
  event_id     uuid   not null unique,
  dataset_id   uuid,
  case_id      uuid,                    -- no foreign key: audit never depends on app
  type         text   not null,
  actor_kind   text   not null check (actor_kind in ('system', 'person')),
  actor_id     uuid,
  occurred_at  timestamptz not null,
  body_jcs     text   not null,         -- the exact canonical JSON that was hashed
  content_hash text   not null check (content_hash ~ '^[0-9a-f]{64}$'),
  prev_hash    text   not null check (prev_hash ~ '^[0-9a-f]{64}$'),
  hash         text   not null check (hash ~ '^[0-9a-f]{64}$'),
  recorded_at  timestamptz not null default now(),
  primary key (chain_id, seq),
  unique (chain_id, prev_hash),         -- one successor per event: no forks
  unique (chain_id, hash)
);

create index evidence_events_case_idx    on audit.evidence_events (case_id, seq);
create index evidence_events_dataset_idx on audit.evidence_events (dataset_id, seq);
create index evidence_events_type_idx    on audit.evidence_events (type);

comment on table audit.evidence_events is
  'Append-only, hash-chained log of every case event (R6, ADR-7). Rows are never updated or deleted.';

-- Append-only, enforced twice: no update/delete grants (below) and a trigger.
create function audit.refuse_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'audit.evidence_events is append-only (R6): % refused', tg_op
    using errcode = 'insufficient_privilege';
end
$$;

create trigger evidence_events_no_update before update on audit.evidence_events
  for each row execute function audit.refuse_mutation();
create trigger evidence_events_no_delete before delete on audit.evidence_events
  for each row execute function audit.refuse_mutation();
create trigger evidence_events_no_truncate before truncate on audit.evidence_events
  for each statement execute function audit.refuse_mutation();

-- RFC 8785 (JCS) canonical JSON, restricted to what evidence needs: object keys sorted by
-- UTF-8 bytes (identical to UTF-16 code-unit order for the ASCII keys our contracts use),
-- Postgres string escaping (which matches JCS for every code point), integers only.
create function audit.jcs(v jsonb) returns text
language plpgsql immutable strict as $$
declare
  t   text := jsonb_typeof(v);
  acc text;
begin
  if t = 'object' then
    select coalesce(string_agg(to_json(e.key)::text || ':' || audit.jcs(e.value), ','
                               order by convert_to(e.key, 'UTF8')), '')
      into acc
      from jsonb_each(v) as e(key, value);
    return '{' || acc || '}';
  elsif t = 'array' then
    select coalesce(string_agg(audit.jcs(e.value), ',' order by e.ord), '')
      into acc
      from jsonb_array_elements(v) with ordinality as e(value, ord);
    return '[' || acc || ']';
  elsif t = 'string' then
    return to_json(v #>> '{}')::text;
  elsif t = 'number' then
    if v::text !~ '^-?[0-9]+$' then
      raise exception 'audit.jcs: only integers are allowed in evidence, got %', v::text;
    end if;
    return v::text;
  elsif t = 'boolean' then
    return v::text;
  else
    return 'null';
  end if;
end
$$;

create function audit.content_hash(p_body_jcs text) returns text
language sql immutable strict as $$
  select encode(sha256(convert_to(p_body_jcs, 'UTF8')), 'hex')
$$;

create function audit.chain_hash(p_prev_hash text, p_content_hash text) returns text
language sql immutable strict as $$
  select encode(sha256(convert_to(p_prev_hash || p_content_hash, 'UTF8')), 'hex')
$$;

create function audit.genesis_hash() returns text
language sql immutable as $$
  select repeat('0', 64)
$$;

-- The one way to write evidence. Canonicalizes, hashes, links to the chain tip under a
-- per-chain lock, and inserts. Callers that computed the content hash themselves pass it
-- in, so a Python/SQL canonicalization drift is caught at write time, not at audit time.
create function audit.append_evidence(
  p_chain_id              uuid,
  p_envelope              jsonb,
  p_expected_content_hash text default null
) returns table (seq bigint, content_hash text, hash text)
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_body_jcs     text;
  v_content_hash text;
  v_prev_hash    text;
  v_prev_seq     bigint;
  v_seq          bigint;
  v_hash         text;
  v_actor        jsonb;
begin
  if p_envelope is null or jsonb_typeof(p_envelope) <> 'object' then
    raise exception 'append_evidence: envelope must be a JSON object';
  end if;
  if p_envelope ? 'seq' or p_envelope ? 'prev_hash' or p_envelope ? 'hash' or p_envelope ? 'content_hash' then
    raise exception 'append_evidence: envelope must not carry chain fields (seq, prev_hash, hash, content_hash)';
  end if;
  if (p_envelope ->> 'chain_id')::uuid is distinct from p_chain_id then
    raise exception 'append_evidence: envelope.chain_id % does not match chain %',
      p_envelope ->> 'chain_id', p_chain_id;
  end if;
  if p_envelope ->> 'event_id' is null or p_envelope ->> 'type' is null
     or p_envelope ->> 'occurred_at' is null or jsonb_typeof(p_envelope -> 'actor') <> 'object' then
    raise exception 'append_evidence: envelope needs event_id, type, occurred_at and actor';
  end if;
  v_actor := p_envelope -> 'actor';
  if (v_actor ->> 'kind') not in ('system', 'person') then
    raise exception 'append_evidence: actor.kind must be system or person';
  end if;

  v_body_jcs     := audit.jcs(p_envelope);
  v_content_hash := audit.content_hash(v_body_jcs);
  if p_expected_content_hash is not null and p_expected_content_hash <> v_content_hash then
    raise exception 'append_evidence: content hash mismatch (caller %, database %)',
      p_expected_content_hash, v_content_hash;
  end if;

  -- Serialize appenders on this chain, then read the tip.
  perform 1 from audit.chains c where c.id = p_chain_id for update;
  if not found then
    raise exception 'append_evidence: unknown chain %', p_chain_id;
  end if;

  select e.seq, e.hash
    into v_prev_seq, v_prev_hash
    from audit.evidence_events e
   where e.chain_id = p_chain_id
   order by e.seq desc
   limit 1;

  if v_prev_seq is null then
    v_seq       := 1;
    v_prev_hash := audit.genesis_hash();
  else
    v_seq := v_prev_seq + 1;
  end if;
  v_hash := audit.chain_hash(v_prev_hash, v_content_hash);

  insert into audit.evidence_events
    (chain_id, seq, event_id, dataset_id, case_id, type, actor_kind, actor_id, occurred_at,
     body_jcs, content_hash, prev_hash, hash)
  values
    (p_chain_id, v_seq, (p_envelope ->> 'event_id')::uuid, (p_envelope ->> 'dataset_id')::uuid,
     (p_envelope ->> 'case_id')::uuid, p_envelope ->> 'type', v_actor ->> 'kind',
     (v_actor ->> 'id')::uuid, (p_envelope ->> 'occurred_at')::timestamptz,
     v_body_jcs, v_content_hash, v_prev_hash, v_hash);

  return query select v_seq, v_content_hash, v_hash;
end
$$;

-- Recompute the whole chain. The dashboard's "chain intact" badge and the five-minute
-- verify job call this; on a break it names the first bad row.
create function audit.verify_chain(p_chain_id uuid)
returns table (ok boolean, checked bigint, first_bad_seq bigint, detail text)
language plpgsql stable security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  r              record;
  v_prev         text   := audit.genesis_hash();
  v_expected_seq bigint := 1;
  v_n            bigint := 0;
begin
  for r in
    select e.seq, e.prev_hash, e.content_hash, e.hash, e.body_jcs
      from audit.evidence_events e
     where e.chain_id = p_chain_id
     order by e.seq
  loop
    if r.seq <> v_expected_seq then
      return query select false, v_n, r.seq, format('gap: expected seq %s, found %s', v_expected_seq, r.seq);
      return;
    end if;
    if r.prev_hash <> v_prev then
      return query select false, v_n, r.seq, 'prev_hash does not match the previous row''s hash';
      return;
    end if;
    if audit.content_hash(r.body_jcs) <> r.content_hash then
      return query select false, v_n, r.seq, 'content_hash does not match body_jcs (body edited)';
      return;
    end if;
    if audit.chain_hash(r.prev_hash, r.content_hash) <> r.hash then
      return query select false, v_n, r.seq, 'hash does not match prev_hash + content_hash';
      return;
    end if;
    v_prev         := r.hash;
    v_expected_seq := v_expected_seq + 1;
    v_n            := v_n + 1;
  end loop;
  return query select true, v_n, null::bigint, 'chain intact';
end
$$;

-- The tip, for the GitHub witness commit and the dashboard badge.
create function audit.chain_tip(p_chain_id uuid)
returns table (seq bigint, hash text)
language sql stable
set search_path = pg_catalog, pg_temp
as $$
  select e.seq, e.hash
    from audit.evidence_events e
   where e.chain_id = p_chain_id
   order by e.seq desc
   limit 1
$$;

-- Access. Reads for the dashboard; inserts only through append_evidence or the service
-- role; no update or delete grant for anyone.
alter table audit.chains          enable row level security;
alter table audit.evidence_events enable row level security;

create policy chains_read   on audit.chains          for select to authenticated using (true);
create policy evidence_read on audit.evidence_events for select to authenticated using (true);

revoke all on audit.chains, audit.evidence_events from public, anon, authenticated, service_role;
grant select on audit.chains, audit.evidence_events to authenticated, service_role;
grant insert on audit.chains          to service_role;
grant insert on audit.evidence_events to service_role;

revoke execute on function audit.append_evidence(uuid, jsonb, text) from public;
grant  execute on function audit.append_evidence(uuid, jsonb, text) to service_role;
grant  execute on function audit.verify_chain(uuid), audit.chain_tip(uuid), audit.jcs(jsonb),
                           audit.content_hash(text), audit.chain_hash(text, text), audit.genesis_hash()
       to authenticated, service_role;
