-- app: the three SQL functions that protect money (ADR-1, ADR-9, ADR-10), and the
-- transition table they consult. Owner: Abhi.
--
--   app.apply_case_event()  the one door for every state change (R1, R3, R6, R7)
--   app.release_case()      four-eyes release with the signed-in user's token (R5)
--   audit.append_evidence() the evidence chain (0001_evidence_chain.sql)
--
-- Both functions run as their owner (security definer) so the tables can stay closed to
-- direct writes, and both read the caller from auth.uid(): a person can only act as
-- themselves, and the service role (no user) can only act as the system.

-- Helpers ---------------------------------------------------------------------------------

create function app.rfc3339(p_ts timestamptz) returns text
language sql immutable strict as $$
  select to_char(p_ts at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
$$;

create function app.add_business_days(p_from timestamptz, p_days integer) returns timestamptz
language plpgsql immutable strict as $$
declare
  v_ts   timestamptz := p_from;
  v_left integer     := p_days;
begin
  while v_left > 0 loop
    v_ts := v_ts + interval '1 day';
    if extract(isodow from v_ts at time zone 'UTC') < 6 then
      v_left := v_left - 1;
    end if;
  end loop;
  return v_ts;
end
$$;

create function app.has_role(p_user uuid, p_role app.user_role) returns boolean
language sql stable
set search_path = pg_catalog, pg_temp
as $$
  select exists (select 1 from app.user_roles r where r.user_id = p_user and r.role = p_role)
$$;

-- Guards named in app.case_transitions. Each one reads only the case row, the actor and
-- the event body, so the table and this function together are the whole state machine.
create function app.eval_guard(p_guard text, p_case app.cases, p_actor_id uuid, p_body jsonb)
returns boolean
language plpgsql stable
set search_path = pg_catalog, pg_temp
as $$
declare
  v_reason_given boolean := length(btrim(coalesce(p_body ->> 'reason', ''))) > 0;
  v_no_person    boolean := coalesce(p_body ->> 'result', '') in ('no_answer', 'voicemail', 'busy', 'failed', 'machine', 'declined');
  v_is_changer   boolean := p_actor_id is not null and p_actor_id = any (p_case.changer_ids);
  v_is_reviewer  boolean := p_actor_id is not null and p_case.reviewed_by is not null and p_actor_id = p_case.reviewed_by;
  v_is_approver  boolean := p_actor_id is not null and app.has_role(p_actor_id, 'approver');
begin
  case p_guard
    when 'always' then
      return true;
    when 'no_unfinished_case' then
      return not exists (
        select 1 from app.cases o
         where o.dataset_id = p_case.dataset_id and o.vendor_id = p_case.vendor_id
           and o.id <> p_case.id and o.state <> 'released');
    when 'readback_ok' then
      return coalesce((p_body ->> 'readback_ok')::boolean, false);
    when 'number_selected' then
      return (p_body ->> 'contact_point_id') is not null;
    when 'answers_recorded' then
      return p_body ->> 'result' = 'answers_recorded';
    when 'no_person_tries_left' then
      return v_no_person and p_case.call_attempts < p_case.max_call_attempts;
    when 'no_person_tries_exhausted' then
      return v_no_person and p_case.call_attempts >= p_case.max_call_attempts;
    when 'in_calling_window' then
      return coalesce((p_body ->> 'in_window')::boolean, false);
    when 'outcome_verified' then
      return p_body ->> 'outcome' = 'verified';
    when 'outcome_not_verified' then
      return p_body ->> 'outcome' = 'not_verified';
    when 'outcome_needs_review' then
      return p_body ->> 'outcome' = 'needs_review';
    when 'reason_given' then
      return v_reason_given;
    when 'reason_given_not_changer' then
      return v_reason_given and not v_is_changer;
    when 'first_approver' then
      -- Unknown changer (ADR-10): the first of two approvers records an approval and the
      -- case stays in awaiting_release.
      return v_is_approver and p_case.has_unknown_changer and not v_is_changer and not v_is_reviewer
         and v_reason_given
         and not exists (select 1 from app.releases r
                          where r.case_id = p_case.id and r.case_version = p_case.version and r.action = 'approve');
    when 'four_eyes' then
      return v_is_approver and not v_is_changer and not v_is_reviewer and v_reason_given
         and (not p_case.has_unknown_changer
              or exists (select 1 from app.releases r
                          where r.case_id = p_case.id and r.case_version = p_case.version
                            and r.action = 'approve' and r.actor_id <> p_actor_id));
    when 'four_eyes_reject' then
      return v_is_approver and not v_is_changer and v_reason_given;
    else
      raise exception 'app.eval_guard: unknown guard %', p_guard;
  end case;
end
$$;

-- apply_case_event -----------------------------------------------------------------------

create function app.apply_case_event(
  p_case_id         uuid,
  p_event_type      text,
  p_actor           jsonb,
  p_idempotency_key text,
  p_body            jsonb default '{}'::jsonb
) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_uid         uuid := auth.uid();
  v_actor_kind  app.actor_kind;
  v_actor_id    uuid;
  v_receipt     app.case_event_receipts%rowtype;
  v_case        app.cases%rowtype;
  v_change      app.bank_change_events%rowtype;
  v_dataset     app.datasets%rowtype;
  v_from        app.case_state;
  v_to          app.case_state;
  v_guard       text;
  v_n_event     integer;
  v_n_actor     integer;
  v_n_guard     integer;
  v_envelope    jsonb;
  v_ev          record;
  v_result      jsonb;
  v_now         timestamptz := clock_timestamp();
  v_event_id    uuid := gen_random_uuid();
  v_is_new      boolean := false;
  v_outcome     app.case_outcome;
begin
  -- 0. Arguments ---------------------------------------------------------------------------
  if coalesce(p_event_type, '') = '' then
    raise exception 'apply_case_event: event_type is required';
  end if;
  if coalesce(p_idempotency_key, '') = '' then
    raise exception 'apply_case_event: idempotency_key is required';
  end if;
  if p_body is null or jsonb_typeof(p_body) <> 'object' then
    raise exception 'apply_case_event: body must be a JSON object';
  end if;
  if p_actor is null or jsonb_typeof(p_actor) <> 'object' then
    raise exception 'apply_case_event: actor must be {"kind": "system"|"person", "id": uuid|null}';
  end if;
  v_actor_kind := (p_actor ->> 'kind')::app.actor_kind;
  v_actor_id   := (p_actor ->> 'id')::uuid;

  -- 1. Who is calling. A person is the signed-in user; the system has no user (ADR-9).
  if v_actor_kind = 'person' then
    if v_uid is null or v_actor_id is null or v_uid <> v_actor_id then
      raise exception 'apply_case_event: person events must be made by the signed-in user'
        using errcode = 'insufficient_privilege';
    end if;
  elsif v_uid is not null then
    raise exception 'apply_case_event: a signed-in user cannot act as the system'
      using errcode = 'insufficient_privilege';
  end if;

  -- 2. Idempotency, fast path (re-checked under the case lock below).
  select * into v_receipt from app.case_event_receipts r where r.idempotency_key = p_idempotency_key;
  if found then
    return v_receipt.result || jsonb_build_object('status', 'already_applied');
  end if;

  -- 3. Load and lock the case; on change_detected, find the vendor's open case or open one.
  if p_event_type = 'change_detected' then
    select * into v_change from app.bank_change_events ce where ce.id = (p_body ->> 'change_event_id')::uuid;
    if not found then
      raise exception 'apply_case_event: change_detected needs body.change_event_id';
    end if;
    perform 1 from app.vendors v where v.id = v_change.vendor_id for update;   -- serialize per vendor
    select * into v_case from app.cases c
     where c.dataset_id = v_change.dataset_id and c.vendor_id = v_change.vendor_id and c.state <> 'released'
     order by c.opened_at desc limit 1
     for update;
    if not found then
      v_is_new := true;
      insert into app.cases (dataset_id, vendor_id, change_event_id, state, entered_by, changer_ids,
                             has_unknown_changer, policy_version, deadline_at)
      values (v_change.dataset_id, v_change.vendor_id, v_change.id, 'open', v_change.changed_by,
              case when v_change.changed_by is null then '{}'::uuid[] else array[v_change.changed_by] end,
              v_change.changed_by is null, coalesce(p_body ->> 'policy_version', 'v0'),
              app.add_business_days(v_now, 3))
      returning * into v_case;
    end if;
  else
    if p_case_id is null then
      raise exception 'apply_case_event: case_id is required for %', p_event_type;
    end if;
    select * into v_case from app.cases c where c.id = p_case_id for update;
    if not found then
      raise exception 'apply_case_event: unknown case %', p_case_id;
    end if;
  end if;
  if p_case_id is not null and p_case_id <> v_case.id then
    raise exception 'apply_case_event: case % is not the vendor''s open case (%)', p_case_id, v_case.id;
  end if;

  select * into v_receipt from app.case_event_receipts r where r.idempotency_key = p_idempotency_key;
  if found then
    return v_receipt.result || jsonb_build_object('status', 'already_applied');
  end if;

  v_from := case when v_is_new then null else v_case.state end;

  -- 4. People must have seen the current version (optimistic concurrency on the change generation).
  if v_actor_kind = 'person' then
    if (p_body ->> 'expected_version') is null or (p_body ->> 'expected_version')::integer <> v_case.version then
      raise exception 'apply_case_event: case % changed since you loaded it (now version %)', v_case.id, v_case.version
        using errcode = 'serialization_failure';
    end if;
  end if;

  -- 5. Look up the move: right event for this state, right kind of actor, guard holds.
  select count(*) into v_n_event from app.case_transitions t
   where t.from_state is not distinct from v_from and t.event_type = p_event_type;
  if v_n_event = 0 then
    raise exception 'apply_case_event: no transition from % on %', coalesce(v_from::text, '(new)'), p_event_type
      using errcode = 'check_violation';
  end if;
  select count(*) into v_n_actor from app.case_transitions t
   where t.from_state is not distinct from v_from and t.event_type = p_event_type and t.actor_kind = v_actor_kind;
  if v_n_actor = 0 then
    raise exception 'apply_case_event: % on % may not be made by the %', p_event_type, coalesce(v_from::text, '(new)'), v_actor_kind
      using errcode = 'insufficient_privilege';
  end if;
  select count(*), min(t.to_state::text), min(t.guard_key) into v_n_guard, v_to, v_guard
    from app.case_transitions t
   where t.from_state is not distinct from v_from and t.event_type = p_event_type and t.actor_kind = v_actor_kind
     and app.eval_guard(t.guard_key, v_case, v_actor_id, p_body);
  if v_n_guard = 0 then
    raise exception 'apply_case_event: % on % refused: guard failed', p_event_type, coalesce(v_from::text, '(new)')
      using errcode = 'check_violation';
  elsif v_n_guard > 1 then
    raise exception 'apply_case_event: % on % matches % transitions; guards must be exclusive', p_event_type, v_from, v_n_guard;
  end if;

  -- 6. Side effects, by event ------------------------------------------------------------
  case p_event_type
    when 'change_detected' then
      if not v_is_new then
        -- Another change while the case is unfinished: new generation, earlier decision void.
        update app.decisions d set voided_at = v_now where d.case_id = v_case.id and d.voided_at is null;
        update app.cases c
           set version             = c.version + 1,
               change_event_id     = v_change.id,
               outcome             = null,
               reviewed_by         = null,
               call_attempts       = 0,
               changer_ids         = case when v_change.changed_by is null or v_change.changed_by = any (c.changer_ids)
                                          then c.changer_ids else c.changer_ids || v_change.changed_by end,
               has_unknown_changer = c.has_unknown_changer or v_change.changed_by is null
         where c.id = v_case.id
         returning * into v_case;
        update app.timers t set status = 'cancelled', cancelled_at = v_now
         where t.case_id = v_case.id and t.status = 'scheduled' and t.kind <> 'deadline';
        update app.outbox o set status = 'failed', last_error = 'superseded by a new change', done_at = v_now
         where o.case_id = v_case.id and o.status = 'pending';
      else
        insert into app.timers (dataset_id, case_id, kind, fire_at)
        values (v_case.dataset_id, v_case.id, 'deadline', v_case.deadline_at);
      end if;
      -- The hold goes on (or is confirmed again) before anything else happens (R1).
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload)
      values (v_case.dataset_id, v_case.id, 'place_hold', format('%s:hold:v%s', v_case.id, v_case.version),
              jsonb_build_object('vendor_id', v_case.vendor_id, 'version', v_case.version))
      on conflict (idempotency_key) do nothing;

    when 'hold_confirmed' then
      insert into app.holds (dataset_id, case_id, vendor_id, ramp_ref, status, original_schedule, placed_at, confirmed_at)
      values (v_case.dataset_id, v_case.id, v_case.vendor_id, p_body ->> 'ramp_ref', 'confirmed',
              coalesce(p_body -> 'original_schedule', '[]'::jsonb),
              coalesce((p_body ->> 'placed_at')::timestamptz, v_now), v_now)
      on conflict (case_id) where released_at is null
      do update set ramp_ref = excluded.ramp_ref, status = 'confirmed', confirmed_at = excluded.confirmed_at,
                    original_schedule = case when jsonb_array_length(excluded.original_schedule) > 0
                                             then excluded.original_schedule else app.holds.original_schedule end;
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload)
      values (v_case.dataset_id, v_case.id, 'select_number', format('%s:select:v%s', v_case.id, v_case.version),
              jsonb_build_object('vendor_id', v_case.vendor_id, 'change_event_id', v_case.change_event_id))
      on conflict (idempotency_key) do nothing;

    when 'hold_failed' then
      insert into app.holds (dataset_id, case_id, vendor_id, status, last_error, placed_at)
      values (v_case.dataset_id, v_case.id, v_case.vendor_id, 'failed', p_body ->> 'last_error', v_now)
      on conflict (case_id) where released_at is null
      do update set status = 'failed', last_error = excluded.last_error;
      insert into app.alerts (dataset_id, case_id, kind, severity, message)
      values (v_case.dataset_id, v_case.id, 'hold_not_confirmed', 'critical', 'Hold not confirmed in Ramp; case sent to review');

    when 'number_resolved' then
      update app.cases c set call_attempts = c.call_attempts + 1 where c.id = v_case.id returning * into v_case;
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload)
      values (v_case.dataset_id, v_case.id, 'place_call', format('%s:call:%s', v_case.id, v_case.call_attempts),
              p_body || jsonb_build_object('attempt', v_case.call_attempts))
      on conflict (idempotency_key) do nothing;

    when 'no_provable_number' then
      insert into app.alerts (dataset_id, case_id, kind, severity, message)
      values (v_case.dataset_id, v_case.id, 'no_provable_number', 'warning', 'No provable phone number; a person must verify');

    when 'call_ended' then
      if v_to = 'deciding' then
        insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload)
        values (v_case.dataset_id, v_case.id, 'decide', format('%s:decide:v%s:%s', v_case.id, v_case.version, v_case.call_attempts),
                jsonb_build_object('call_id', p_body ->> 'call_id'))
        on conflict (idempotency_key) do nothing;
      elsif v_to = 'retry_wait' then
        insert into app.timers (dataset_id, case_id, kind, fire_at, payload)
        values (v_case.dataset_id, v_case.id, 'retry_call',
                coalesce((p_body ->> 'retry_at')::timestamptz, v_now + interval '2 hours'),
                jsonb_build_object('after_attempt', v_case.call_attempts));
      end if;

    when 'retry_timer_fired' then
      update app.cases c set call_attempts = c.call_attempts + 1 where c.id = v_case.id returning * into v_case;
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload)
      values (v_case.dataset_id, v_case.id, 'place_call', format('%s:call:%s', v_case.id, v_case.call_attempts),
              jsonb_build_object('attempt', v_case.call_attempts))
      on conflict (idempotency_key) do nothing;

    when 'decision_made' then
      v_outcome := (p_body ->> 'outcome')::app.case_outcome;
      insert into app.decisions (dataset_id, case_id, case_version, outcome, rules_fired, inputs_hash,
                                 engine_version, strict_mode, last_four_result, source)
      values (v_case.dataset_id, v_case.id, v_case.version, v_outcome,
              coalesce(array(select jsonb_array_elements_text(p_body -> 'rules_fired')), '{}'),
              p_body ->> 'inputs_hash', p_body ->> 'engine_version',
              coalesce((p_body ->> 'strict_mode')::boolean, false), p_body ->> 'last_four_result',
              coalesce(p_body ->> 'source', 'call'));
      update app.cases c set outcome = v_outcome where c.id = v_case.id returning * into v_case;
      if v_outcome = 'not_verified' then
        insert into app.alerts (dataset_id, case_id, kind, severity, message)
        values (v_case.dataset_id, v_case.id, 'not_verified', 'critical', 'Vendor did not verify the bank change; hold stays on');
      end if;
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload)
      values (v_case.dataset_id, v_case.id, 'notify', format('%s:notify:decision:v%s:%s', v_case.id, v_case.version, v_case.call_attempts),
              jsonb_build_object('outcome', v_outcome::text))
      on conflict (idempotency_key) do nothing;

    when 'review_cleared' then
      update app.cases c set reviewed_by = v_actor_id where c.id = v_case.id returning * into v_case;

    when 'release_co_approved' then
      insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason)
      values (v_case.dataset_id, v_case.id, v_case.version, v_actor_id, 'approve', p_body ->> 'reason');

    when 'release_approved' then
      insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason)
      values (v_case.dataset_id, v_case.id, v_case.version, v_actor_id, 'approve', p_body ->> 'reason');
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload)
      values (v_case.dataset_id, v_case.id, 'release_hold', format('%s:release:v%s', v_case.id, v_case.version),
              jsonb_build_object('vendor_id', v_case.vendor_id))
      on conflict (idempotency_key) do nothing;

    when 'release_rejected' then
      insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason)
      values (v_case.dataset_id, v_case.id, v_case.version, v_actor_id, 'reject', p_body ->> 'reason');

    when 'hold_release_confirmed' then
      update app.holds h set status = 'released', released_at = coalesce(h.released_at, v_now), release_confirmed_at = v_now
       where h.case_id = v_case.id and h.released_at is null;
      update app.timers t set status = 'cancelled', cancelled_at = v_now
       where t.case_id = v_case.id and t.status = 'scheduled';

    when 'deadline_reached' then
      insert into app.alerts (dataset_id, case_id, kind, severity, message)
      values (v_case.dataset_id, v_case.id, 'deadline_reached', 'warning', 'Three business days passed; hold stays on, person must review');

    when 'command_failed' then
      insert into app.alerts (dataset_id, case_id, kind, severity, message)
      values (v_case.dataset_id, v_case.id, 'command_stuck', 'critical',
              format('Command %s failed after %s attempts', p_body ->> 'command', coalesce(p_body ->> 'attempts', '?')));

    else
      null;
  end case;

  -- 7. Move the case.
  update app.cases c
     set state     = v_to,
         closed_at = case when v_to = 'released' then v_now else c.closed_at end
   where c.id = v_case.id
   returning * into v_case;

  -- 8. Evidence (R6). The envelope is canonicalized and hashed by audit.append_evidence.
  select * into v_dataset from app.datasets d where d.id = v_case.dataset_id;
  v_envelope := jsonb_build_object(
    'v', 1,
    'chain_id', v_dataset.chain_id,
    'dataset_id', v_case.dataset_id,
    'case_id', v_case.id,
    'event_id', v_event_id,
    'type', p_event_type,
    'actor', jsonb_build_object('kind', v_actor_kind::text, 'id', v_actor_id),
    'occurred_at', app.rfc3339(v_now),
    'idempotency_key', p_idempotency_key,
    'from_state', v_from::text,
    'to_state', v_to::text,
    'version', v_case.version,
    'body', p_body);
  select * into v_ev from audit.append_evidence(v_dataset.chain_id, v_envelope);

  v_result := jsonb_build_object(
    'status', 'applied',
    'case_id', v_case.id,
    'from_state', v_from::text,
    'to_state', v_to::text,
    'version', v_case.version,
    'event_id', v_event_id,
    'evidence_seq', v_ev.seq,
    'evidence_hash', v_ev.hash);

  insert into app.case_event_receipts (idempotency_key, case_id, event_type, result)
  values (p_idempotency_key, v_case.id, p_event_type, v_result);

  return v_result;
end
$$;

comment on function app.apply_case_event(uuid, text, jsonb, text, jsonb) is
  'The one door for every case state change. Looks up the move in app.case_transitions, checks the actor kind and guard, applies side effects, queues follow-up commands, appends evidence, and is idempotent on idempotency_key.';

-- release_case -------------------------------------------------------------------------------

create function app.release_case(
  p_case_id          uuid,
  p_expected_version integer,
  p_action           app.release_action,
  p_reason           text
) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_uid   uuid := auth.uid();
  v_case  app.cases%rowtype;
  v_body  jsonb;
  v_actor jsonb;
  v_res   jsonb;
begin
  if v_uid is null then
    raise exception 'release_case: sign in first; releases are never made with a server key (ADR-9)'
      using errcode = 'insufficient_privilege';
  end if;
  if length(btrim(coalesce(p_reason, ''))) = 0 then
    raise exception 'release_case: a written reason is required' using errcode = 'check_violation';
  end if;

  select * into v_case from app.cases c where c.id = p_case_id;
  if not found then
    raise exception 'release_case: unknown case %', p_case_id;
  end if;
  if not app.has_role(v_uid, 'approver') then
    raise exception 'release_case: approver role required' using errcode = 'insufficient_privilege';
  end if;
  if v_case.version <> p_expected_version then
    raise exception 'release_case: case % changed since you loaded it (now version %)', p_case_id, v_case.version
      using errcode = 'serialization_failure';
  end if;
  if v_case.state <> 'awaiting_release' then
    raise exception 'release_case: case % is %, not awaiting_release', p_case_id, v_case.state
      using errcode = 'check_violation';
  end if;
  if v_uid = any (v_case.changer_ids) then
    raise exception 'release_case: the person who entered the change cannot release it (R5)'
      using errcode = 'check_violation';
  end if;

  v_actor := jsonb_build_object('kind', 'person', 'id', v_uid);
  v_body  := jsonb_build_object('reason', p_reason, 'expected_version', p_expected_version);

  if p_action = 'reject' then
    return app.apply_case_event(p_case_id, 'release_rejected', v_actor,
             format('%s:reject:v%s:%s', p_case_id, p_expected_version, v_uid), v_body);
  end if;

  if v_uid = v_case.reviewed_by then
    raise exception 'release_case: the reviewer who cleared the case cannot also approve it (R5)'
      using errcode = 'check_violation';
  end if;

  -- Unknown changer (ADR-10): the first approver co-approves; the second releases.
  if v_case.has_unknown_changer and not exists (
       select 1 from app.releases r
        where r.case_id = p_case_id and r.case_version = v_case.version and r.action = 'approve' and r.actor_id <> v_uid) then
    v_res := app.apply_case_event(p_case_id, 'release_co_approved', v_actor,
               format('%s:co_approve:v%s:%s', p_case_id, p_expected_version, v_uid), v_body);
    if v_res ->> 'status' = 'already_applied' then
      return v_res;                                      -- the same approver clicking twice
    end if;
    return v_res || jsonb_build_object('status', 'awaiting_second_approver');
  end if;

  return app.apply_case_event(p_case_id, 'release_approved', v_actor,
           format('%s:approve:v%s:%s', p_case_id, p_expected_version, v_uid), v_body);
end
$$;

comment on function app.release_case(uuid, integer, app.release_action, text) is
  'Four-eyes release with the signed-in user (ADR-9): approver role, not the changer, not the reviewer, version matches; two approvers when the changer is unknown (ADR-10).';

-- Grants -------------------------------------------------------------------------------------

revoke execute on function app.apply_case_event(uuid, text, jsonb, text, jsonb)          from public;
revoke execute on function app.release_case(uuid, integer, app.release_action, text)     from public;
revoke execute on function app.eval_guard(text, app.cases, uuid, jsonb)                  from public;
grant  execute on function app.apply_case_event(uuid, text, jsonb, text, jsonb)          to service_role, authenticated;
grant  execute on function app.release_case(uuid, integer, app.release_action, text)     to authenticated;
grant  execute on function app.has_role(uuid, app.user_role), app.rfc3339(timestamptz),
                           app.add_business_days(timestamptz, integer)                   to service_role, authenticated;
