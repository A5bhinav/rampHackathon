-- app: an email never joins a case that Ramp changed, and the approvals refuse one anyway
-- (specs 0084, 0087, 0090; final review finding C1). Owner: Sami (the door's outcomes) and Abhi
-- (money path, the guard).
--
-- A case verifies its current change. If an email's change joined a case with a Ramp change, the
-- call would verify the email's account while Ramp pays the clerk's, and two approvers could lift
-- the hold for an account nobody verified (R1). The email door now writes nothing for a vendor
-- whose unfinished case has any change that did not come by email, and lists the email with the
-- outcome ramp_case_open, linked to that case. 0013 is applied, so its check is replaced here.
--
-- The backstop, in case a bug or another writer ever does join them: first_approver and
-- four_eyes also refuse when the case's current change came by email and the case is not email
-- only (app.case_email_only, 0015). eval_guard is redefined in full, as 0008, 0011 and 0015 did,
-- so one file shows the whole function; every other branch is exactly 0015's. No transition
-- changes. create or replace keeps the grants on eval_guard (owner only; see 0004).

alter table app.inbound_emails drop constraint inbound_emails_outcome_check;
alter table app.inbound_emails add constraint inbound_emails_outcome_check check (outcome in (
  'pending', 'not_flagged', 'door_off', 'not_demo_vendor', 'no_vendor', 'several_vendors',
  'no_routing_number', 'no_account_number', 'same_as_on_file', 'repeat_request',
  'case_opened', 'joined_case', 'ramp_case_open', 'failed'));

-- The backstop ---------------------------------------------------------------------------------

-- True when the case's current change came in by email and the case is not email only. Then the
-- call verified the email's account, but Ramp's account on file is not (or may not be) the one
-- that email was compared against, so lifting the hold could pay an account nobody verified.
-- Also true for an email case whose vendor had no account on file (rule A): lifting that hold
-- would pay nothing, and refusing keeps it on (fail closed, as spec 0090's open question 1).
create function app.email_release_refused(p_case app.cases) returns boolean
language sql stable
set search_path = pg_catalog, pg_temp
as $$
  select exists (select 1 from app.bank_change_events ce
                  where ce.id = p_case.change_event_id and ce.detected_via = 'email')
         and not app.case_email_only(p_case)
$$;

comment on function app.email_release_refused(app.cases) is
  'Spec 0090 (final review C1): the case''s current change came by email but the case is not email only, so first_approver and four_eyes refuse it.';

revoke execute on function app.email_release_refused(app.cases) from public;
grant  execute on function app.email_release_refused(app.cases) to authenticated, service_role;

-- Guards: 0015's eval_guard, with first_approver and four_eyes refusing such a case -------------

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
    -- Spec 0028: a review is an approver's call. Anyone can sign up (spec 0027), so a review
    -- that needed no role could be made by anyone. The old keys (reason_given,
    -- reason_given_not_changer) are gone: anything still naming one hits "unknown guard".
    when 'approver_reason' then
      return v_is_approver and v_reason_given;
    when 'approver_reason_not_changer' then
      return v_is_approver and v_reason_given and not v_is_changer;
    -- Spec 0090 (final review finding C1): a case verifies its current change. When that came by
    -- email and the case is not email only, Ramp may pay an account the call never verified, so no
    -- approval counts (app.email_release_refused).
    when 'first_approver' then
      return v_is_approver and p_case.has_unknown_changer and not v_is_changer and not v_is_reviewer
         and v_reason_given
         and not app.email_release_refused(p_case)
         and not exists (select 1 from app.releases r
                          where r.case_id = p_case.id and r.case_version = p_case.version and r.action = 'approve');
    when 'four_eyes' then
      return v_is_approver and not v_is_changer and not v_is_reviewer and v_reason_given
         and not app.email_release_refused(p_case)
         and (not p_case.has_unknown_changer
              or exists (select 1 from app.releases r
                          where r.case_id = p_case.id and r.case_version = p_case.version
                            and r.action = 'approve' and r.actor_id <> p_actor_id));
    when 'four_eyes_reject' then
      return v_is_approver and not v_is_changer and v_reason_given;
    -- Spec 0090: two different approvers, each with a written reason, dismiss a blocked
    -- email-only case, whatever has_unknown_changer says. Not the changer nor the reviewer, as
    -- for any release (the releases_four_eyes trigger refuses both too). Only approvals made
    -- while the case is blocked count (app.blocked_approvers).
    when 'email_dismiss_first' then
      return v_is_approver and v_reason_given and not v_is_changer and not v_is_reviewer
         and app.case_email_only(p_case)
         and cardinality(app.blocked_approvers(p_case)) = 0;
    when 'email_dismiss_four_eyes' then
      return v_is_approver and v_reason_given and not v_is_changer and not v_is_reviewer
         and app.case_email_only(p_case)
         and exists (select 1 from unnest(app.blocked_approvers(p_case)) a (actor_id) where a.actor_id <> p_actor_id);
    else
      raise exception 'app.eval_guard: unknown guard %', p_guard;
  end case;
end
$$;
