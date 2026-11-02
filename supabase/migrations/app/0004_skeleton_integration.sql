-- app: integration fixes for the walking skeleton. Owner: Abhi.
-- Spec: specs/0002-skeleton-integration.md (D2, D6, D8).
--
-- D2  One clock per dataset. app.datasets.sim_now holds a lab dataset's simulated time and
--     app.now(dataset) is the only clock the SQL functions read. Demo and dev datasets can
--     never be given a fake time (a check constraint), and evidence keeps the real time too
--     (audit.evidence_events.recorded_at).
-- D6  Calls are bound to the case's current generation: a new event call_started pins
--     cases.current_call_id, and call_ended is accepted only for that call. place_call keys
--     carry the version, so a re-held case's first call no longer collides with the old one.
-- D8  hold_confirmed is allowed from held (a re-hold after another change), and the decide
--     command carries the stored call_ended body, so deciding never reads another slice's
--     tables.
--
-- Earlier migrations are never edited; this file replaces app.eval_guard and
-- app.apply_case_event with `create or replace`.

-- D2: the dataset clock --------------------------------------------------------------------

alter table app.datasets
  add column sim_now timestamptz,
  add constraint datasets_sim_now_lab_only check (sim_now is null or kind = 'lab');

comment on column app.datasets.sim_now is
  'Lab datasets only: the scenario''s simulated time. Null means real time. Read through app.now().';

create function app.now(p_dataset_id uuid) returns timestamptz
language sql volatile
set search_path = pg_catalog, pg_temp
as $$
  select coalesce(
    (select case when d.kind = 'lab' then d.sim_now end from app.datasets d where d.id = p_dataset_id),
    clock_timestamp())
$$;

comment on function app.now(uuid) is
  'The one clock for a dataset: its simulated time for a lab dataset with sim_now set, otherwise the real clock.';

-- Set or move a lab dataset's clock. Forward only, so time-based rules can't be replayed
-- backwards into a different answer. The attack lab calls this as the service role.
create function app.advance_clock(p_dataset_id uuid, p_to timestamptz) returns timestamptz
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  d app.datasets%rowtype;
begin
  select * into d from app.datasets where id = p_dataset_id for update;
  if not found then
    raise exception 'advance_clock: unknown dataset %', p_dataset_id;
  end if;
  if d.kind <> 'lab' then
    raise exception 'advance_clock: only lab datasets have a simulated clock (dataset % is %)', p_dataset_id, d.kind
      using errcode = 'check_violation';
  end if;
  if p_to is null then
    raise exception 'advance_clock: a time is required';
  end if;
  if d.sim_now is not null and p_to < d.sim_now then
    raise exception 'advance_clock: the clock only moves forward (% is before %)', p_to, d.sim_now
      using errcode = 'check_violation';
  end if;
  update app.datasets set sim_now = p_to where id = p_dataset_id;
  return p_to;
end
$$;

-- D6: the current call -----------------------------------------------------------------------

alter table app.cases add column current_call_id uuid;

comment on column app.cases.current_call_id is
  'The call the voice service accepted for the current attempt (set by call_started). call_ended is accepted only for this call.';

-- New transitions ----------------------------------------------------------------------------

insert into app.case_transitions (from_state, event_type, actor_kind, guard_key, to_state, description) values
  ('calling', 'call_started',   'system', 'call_attempt_matches', 'calling', 'The voice service accepted the call; pins the current call'),
  ('held',    'hold_confirmed', 'system', 'readback_ok',          'held',    'Hold confirmed again after another change (version + 1)');

-- Guards -------------------------------------------------------------------------------------

create or replace function app.eval_guard(p_guard text, p_case app.cases, p_actor_id uuid, p_body jsonb)
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
  -- D6: a call report counts only for the call that call_started pinned.
  v_current_call boolean := p_case.current_call_id is not null
                            and (p_body ->> 'call_id') is not null
                            and (p_body ->> 'call_id')::uuid = p_case.current_call_id;
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
    when 'call_attempt_matches' then
      return (p_body ->> 'call_id') is not null
         and (p_body ->> 'attempt') is not null
         and (p_body ->> 'attempt')::integer = p_case.call_attempts;
    when 'answers_recorded' then
      return v_current_call and p_body ->> 'result' = 'answers_recorded';
    when 'no_person_tries_left' then
      return v_current_call and v_no_person and p_case.call_attempts < p_case.max_call_attempts;
    when 'no_person_tries_exhausted' then
      return v_current_call and v_no_person and p_case.call_attempts >= p_case.max_call_attempts;
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

-- apply_case_event, with the dataset clock, version-qualified call keys and call binding ------

create or replace function app.apply_case_event(
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
  v_now         timestamptz;              -- the dataset's clock, set once the dataset is known
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
    v_now := app.now(v_change.dataset_id);
    perform 1 from app.vendors v where v.id = v_change.vendor_id for update;   -- serialize per vendor
    select * into v_case from app.cases c
     where c.dataset_id = v_change.dataset_id and c.vendor_id = v_change.vendor_id and c.state <> 'released'
     order by c.opened_at desc limit 1
     for update;
    if not found then
      v_is_new := true;
      insert into app.cases (dataset_id, vendor_id, change_event_id, state, entered_by, changer_ids,
                             has_unknown_changer, policy_version, deadline_at, opened_at)
      values (v_change.dataset_id, v_change.vendor_id, v_change.id, 'open', v_change.changed_by,
              case when v_change.changed_by is null then '{}'::uuid[] else array[v_change.changed_by] end,
              v_change.changed_by is null, coalesce(p_body ->> 'policy_version', 'v0'),
              app.add_business_days(v_now, 3), v_now)
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
    v_now := app.now(v_case.dataset_id);
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

  -- 6. Side effects, by event. Every time written here is the dataset's clock (v_now). ------
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
               current_call_id     = null,
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
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload, run_after)
      values (v_case.dataset_id, v_case.id, 'place_hold', format('%s:hold:v%s', v_case.id, v_case.version),
              jsonb_build_object('vendor_id', v_case.vendor_id, 'version', v_case.version), v_now)
      on conflict (idempotency_key) do nothing;

    when 'hold_confirmed' then
      insert into app.holds (dataset_id, case_id, vendor_id, ramp_ref, status, original_schedule, placed_at, confirmed_at)
      values (v_case.dataset_id, v_case.id, v_case.vendor_id, p_body ->> 'ramp_ref', 'confirmed',
              coalesce(p_body -> 'original_schedule', '[]'::jsonb),
              coalesce((p_body ->> 'placed_at')::timestamptz, v_now), v_now)
      on conflict (case_id) where released_at is null
      do update set ramp_ref     = coalesce(excluded.ramp_ref, app.holds.ramp_ref),
                    status       = 'confirmed',
                    confirmed_at = excluded.confirmed_at,
                    placed_at    = coalesce(app.holds.placed_at, excluded.placed_at),
                    -- D5: the first saved schedule wins; a re-hold never overwrites it
                    original_schedule = case when jsonb_array_length(app.holds.original_schedule) > 0
                                             then app.holds.original_schedule else excluded.original_schedule end;
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload, run_after)
      values (v_case.dataset_id, v_case.id, 'select_number', format('%s:select:v%s', v_case.id, v_case.version),
              jsonb_build_object('vendor_id', v_case.vendor_id, 'change_event_id', v_case.change_event_id,
                                 'version', v_case.version), v_now)
      on conflict (idempotency_key) do nothing;

    when 'hold_failed' then
      insert into app.holds (dataset_id, case_id, vendor_id, status, last_error, placed_at)
      values (v_case.dataset_id, v_case.id, v_case.vendor_id, 'failed', p_body ->> 'last_error', v_now)
      on conflict (case_id) where released_at is null
      do update set status = 'failed', last_error = excluded.last_error;
      insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at)
      values (v_case.dataset_id, v_case.id, 'hold_not_confirmed', 'critical',
              'Hold not confirmed in Ramp; case sent to review', v_now);

    when 'number_resolved' then
      update app.cases c set call_attempts = c.call_attempts + 1 where c.id = v_case.id returning * into v_case;
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload, run_after)
      values (v_case.dataset_id, v_case.id, 'place_call',
              format('%s:call:v%s:%s', v_case.id, v_case.version, v_case.call_attempts),
              p_body || jsonb_build_object('attempt', v_case.call_attempts, 'version', v_case.version), v_now)
      on conflict (idempotency_key) do nothing;

    when 'no_provable_number' then
      insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at)
      values (v_case.dataset_id, v_case.id, 'no_provable_number', 'warning',
              'No provable phone number; a person must verify', v_now);

    when 'call_started' then
      update app.cases c set current_call_id = (p_body ->> 'call_id')::uuid where c.id = v_case.id returning * into v_case;

    when 'call_ended' then
      if v_to = 'deciding' then
        -- D8: the decide command carries the stored body (answers with digits hashed,
        -- last_four_result, checks), so deciding never reads another slice's tables.
        insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload, run_after)
        values (v_case.dataset_id, v_case.id, 'decide', format('%s:decide:v%s:%s', v_case.id, v_case.version, v_case.call_attempts),
                p_body || jsonb_build_object('version', v_case.version), v_now)
        on conflict (idempotency_key) do nothing;
      elsif v_to = 'retry_wait' then
        insert into app.timers (dataset_id, case_id, kind, fire_at, payload)
        values (v_case.dataset_id, v_case.id, 'retry_call',
                coalesce((p_body ->> 'retry_at')::timestamptz, v_now + interval '2 hours'),
                jsonb_build_object('after_attempt', v_case.call_attempts, 'version', v_case.version));
      end if;

    when 'retry_timer_fired' then
      update app.cases c set call_attempts = c.call_attempts + 1 where c.id = v_case.id returning * into v_case;
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload, run_after)
      values (v_case.dataset_id, v_case.id, 'place_call',
              format('%s:call:v%s:%s', v_case.id, v_case.version, v_case.call_attempts),
              jsonb_build_object('attempt', v_case.call_attempts, 'version', v_case.version), v_now)
      on conflict (idempotency_key) do nothing;

    when 'decision_made' then
      v_outcome := (p_body ->> 'outcome')::app.case_outcome;
      insert into app.decisions (dataset_id, case_id, case_version, outcome, rules_fired, inputs_hash,
                                 engine_version, strict_mode, last_four_result, source, decided_at)
      values (v_case.dataset_id, v_case.id, v_case.version, v_outcome,
              coalesce(array(select jsonb_array_elements_text(p_body -> 'rules_fired')), '{}'),
              p_body ->> 'inputs_hash', p_body ->> 'engine_version',
              coalesce((p_body ->> 'strict_mode')::boolean, false), p_body ->> 'last_four_result',
              coalesce(p_body ->> 'source', 'call'), v_now);
      update app.cases c set outcome = v_outcome where c.id = v_case.id returning * into v_case;
      if v_outcome = 'not_verified' then
        insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at)
        values (v_case.dataset_id, v_case.id, 'not_verified', 'critical',
                'Vendor did not verify the bank change; hold stays on', v_now);
      end if;
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload, run_after)
      values (v_case.dataset_id, v_case.id, 'notify', format('%s:notify:decision:v%s:%s', v_case.id, v_case.version, v_case.call_attempts),
              jsonb_build_object('outcome', v_outcome::text), v_now)
      on conflict (idempotency_key) do nothing;

    when 'review_cleared' then
      update app.cases c set reviewed_by = v_actor_id where c.id = v_case.id returning * into v_case;

    when 'release_co_approved' then
      insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason, created_at)
      values (v_case.dataset_id, v_case.id, v_case.version, v_actor_id, 'approve', p_body ->> 'reason', v_now);

    when 'release_approved' then
      insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason, created_at)
      values (v_case.dataset_id, v_case.id, v_case.version, v_actor_id, 'approve', p_body ->> 'reason', v_now);
      insert into app.outbox (dataset_id, case_id, command, idempotency_key, payload, run_after)
      values (v_case.dataset_id, v_case.id, 'release_hold', format('%s:release:v%s', v_case.id, v_case.version),
              jsonb_build_object('vendor_id', v_case.vendor_id, 'version', v_case.version), v_now)
      on conflict (idempotency_key) do nothing;

    when 'release_rejected' then
      insert into app.releases (dataset_id, case_id, case_version, actor_id, action, reason, created_at)
      values (v_case.dataset_id, v_case.id, v_case.version, v_actor_id, 'reject', p_body ->> 'reason', v_now);

    when 'hold_release_confirmed' then
      update app.holds h set status = 'released', released_at = coalesce(h.released_at, v_now), release_confirmed_at = v_now
       where h.case_id = v_case.id and h.released_at is null;
      update app.timers t set status = 'cancelled', cancelled_at = v_now
       where t.case_id = v_case.id and t.status = 'scheduled';

    when 'deadline_reached' then
      insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at)
      values (v_case.dataset_id, v_case.id, 'deadline_reached', 'warning',
              'Three business days passed; hold stays on, person must review', v_now);

    when 'command_failed' then
      insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at)
      values (v_case.dataset_id, v_case.id, 'command_stuck', 'critical',
              format('Command %s failed after %s attempts', p_body ->> 'command', coalesce(p_body ->> 'attempts', '?')), v_now);

    else
      null;
  end case;

  -- 7. Move the case.
  update app.cases c
     set state     = v_to,
         closed_at = case when v_to = 'released' then v_now else c.closed_at end
   where c.id = v_case.id
   returning * into v_case;

  -- 8. Evidence (R6). occurred_at is the dataset's clock; recorded_at stays the real clock.
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

-- Grants -------------------------------------------------------------------------------------
-- create or replace keeps the earlier grants on apply_case_event and eval_guard.

revoke execute on function app.now(uuid)                          from public;
revoke execute on function app.advance_clock(uuid, timestamptz)   from public;
grant  execute on function app.now(uuid)                          to service_role, authenticated;
grant  execute on function app.advance_clock(uuid, timestamptz)   to service_role;
