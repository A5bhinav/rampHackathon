-- app: callbacks the vendor asked for (spec 0007). Owner: Abhi.
--
-- The voice report ends a call with result callback_requested and retry_at, the time the
-- vendor asked to be called (contract: CallEndedBody, A5bhinav/rampHackathon#18). It is an
-- attempt like a call nobody answered: with tries left the case waits in retry_wait, and
-- call_ended's existing side effect sets the retry_call timer at retry_at, so the worker
-- redials then (or at the next calling-window opening); with no tries left a person takes
-- over, hold on. The guards stay exclusive: a callback is neither answers_recorded nor a
-- no-person result.

create or replace function app.eval_guard(p_guard text, p_case app.cases, p_actor_id uuid, p_body jsonb)
returns boolean
language plpgsql stable
set search_path = pg_catalog, pg_temp
as $$
declare
  v_reason_given boolean := length(btrim(coalesce(p_body ->> 'reason', ''))) > 0;
  v_no_person    boolean := coalesce(p_body ->> 'result', '') in ('no_answer', 'voicemail', 'busy', 'failed', 'machine', 'declined');
  -- The contract requires retry_at with a callback; without one there is no time to call back.
  v_callback     boolean := p_body ->> 'result' = 'callback_requested' and (p_body ->> 'retry_at') is not null;
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
    -- Spec 0007: a callback the vendor asked for is an attempt, with the same cap as no answer.
    when 'callback_tries_left' then
      return v_current_call and v_callback and p_case.call_attempts < p_case.max_call_attempts;
    when 'callback_tries_exhausted' then
      return v_current_call and v_callback and p_case.call_attempts >= p_case.max_call_attempts;
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


insert into app.case_transitions (from_state, event_type, actor_kind, guard_key, to_state, description) values
  ('calling', 'call_ended', 'system', 'callback_tries_left',      'retry_wait',   'The person asked to be called back; tries left'),
  ('calling', 'call_ended', 'system', 'callback_tries_exhausted', 'needs_review', 'The person asked to be called back; no tries left');
