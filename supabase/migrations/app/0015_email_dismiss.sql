-- app: two people can dismiss a blocked email-only case, which lifts the hold (spec 0090).
-- Owner: Abhi (money path); written for the email door (Sami).
--
-- An email case never touches Ramp: the email door records the requested account but never
-- makes it current. When the vendor denies the change the case goes to blocked, and until now
-- only another change led out of blocked, so catching a fraud froze the real vendor's real
-- account for good. Now two different approvers, each with a written reason, can dismiss such
-- a case while it is email only (app.case_email_only): the first co-approves and the case stays
-- blocked; the second moves it to releasing, which queues release_hold, so bills pay the account
-- already on file.
--
-- Only approvals made while the case is blocked count toward a dismissal (app.blocked_approvers).
-- release_rejected and command_failed keep the version, so approvals given in awaiting_release,
-- or by an earlier dismissal whose release failed, are still at the same version; they don't count.
--
-- The dismiss reuses release_co_approved and release_approved, so apply_case_event and its side
-- effects (the app.releases row; the release_hold command keyed {case}:release:v{n}) and the
-- evidence schema do not change. eval_guard and release_case are redefined in full, as 0008,
-- 0011 and 0005 did, so one file shows each whole function. create or replace keeps the grants
-- (see 0004).

-- Rules B and C read a vendor's changes; the blocked-spell functions read a case's receipts.
create index bank_change_events_vendor_detected_idx on app.bank_change_events (vendor_id, detected_at);
create index case_event_receipts_case_idx on app.case_event_receipts (case_id);

-- Email only ---------------------------------------------------------------------------------

-- True when Ramp never changed for this case, so lifting the hold pays the account that was on
-- file before any email arrived. Every check must hold; anything missing or unclear is false.
create function app.case_email_only(p_case app.cases) returns boolean
language plpgsql stable
set search_path = pg_catalog, pg_temp
as $$
declare
  v_change app.bank_change_events%rowtype;   -- the case's current change
begin
  if p_case.id is null then
    return false;
  end if;

  -- A. The current change came in by email, for this vendor, against an account on file.
  select * into v_change from app.bank_change_events ce where ce.id = p_case.change_event_id;
  if not found or v_change.detected_via <> 'email' or v_change.vendor_id <> p_case.vendor_id
     or v_change.old_account_id is null then
    return false;
  end if;

  -- C. Ramp's account on file is still the one the email was compared against, and it is the
  --    vendor's only current account (two current accounts leave "on file" unclear).
  if not exists (select 1 from app.bank_accounts b
                  where b.id = v_change.old_account_id and b.vendor_id = p_case.vendor_id and b.is_current)
     or exists (select 1 from app.bank_accounts b
                 where b.vendor_id = p_case.vendor_id and b.is_current and b.id <> v_change.old_account_id) then
    return false;
  end if;

  -- B. No Ramp-detected change for the vendor since the case opened.
  if exists (select 1 from app.bank_change_events o
              where o.vendor_id = p_case.vendor_id and o.detected_via <> 'email' and o.detected_at >= p_case.opened_at) then
    return false;
  end if;

  -- D. Every change that joined this case came in by email against that same account. The
  --    evidence log is the one record of which changes joined a case (app.cases keeps only the
  --    latest). This catches a Ramp change that opened the case: detection stamps detected_at
  --    a moment before apply_case_event stamps opened_at, so B can't see it. Extra evidence rows
  --    can only make this refuse more.
  if not exists (select 1 from audit.evidence_events e where e.case_id = p_case.id and e.type = 'change_detected')
     or exists (select 1 from audit.evidence_events e
                 where e.case_id = p_case.id and e.type = 'change_detected'
                   and not exists (select 1 from app.bank_change_events ce
                                    where ce.id = (e.body_jcs::jsonb #>> '{body,change_event_id}')::uuid
                                      and ce.vendor_id = p_case.vendor_id
                                      and ce.detected_via = 'email'
                                      and ce.old_account_id = v_change.old_account_id)) then
    return false;
  end if;

  return true;
end
$$;

comment on function app.case_email_only(app.cases) is
  'Spec 0090: the case is email only (every change on it came in by email, and Ramp''s account on file is still the one the emails were compared against, with no Ramp-detected change since the case opened). Two approvers may then dismiss it from blocked.';

revoke execute on function app.case_email_only(app.cases) from public;
grant  execute on function app.case_email_only(app.cases) to authenticated, service_role;

-- Approvals made while blocked -----------------------------------------------------------------
--
-- Both read app.case_event_receipts, which only apply_case_event writes, and order by the
-- receipt's evidence_seq: assigned under the chain's lock in commit order, so strictly
-- increasing. (A receipt's applied_at is the transaction's start on the real clock, while
-- app.releases.created_at is the dataset clock, so times can't order them.)

-- Where the case's current blocked spell starts: the evidence seq of its latest move into
-- blocked from another state. Null if it was never blocked.
create function app.blocked_entry_seq(p_case_id uuid) returns bigint
language sql stable
set search_path = pg_catalog, pg_temp
as $$
  select max((r.result ->> 'evidence_seq')::bigint)
    from app.case_event_receipts r
   where r.case_id = p_case_id
     and r.result ->> 'to_state' = 'blocked'
     and (r.result ->> 'from_state') is distinct from 'blocked'
$$;

-- Who has approved the case at its current version since that move: the approvals that count
-- toward a dismissal. The set comes from the receipts; each actor from the evidence row the
-- receipt names. event_id is unique there and written with the receipt, so a forged evidence
-- row can't stand in for it, and one with no receipt is never read.
create function app.blocked_approvers(p_case app.cases) returns uuid[]
language sql stable
set search_path = pg_catalog, pg_temp
as $$
  select coalesce(array_agg(e.actor_id order by e.seq), '{}')
    from app.case_event_receipts r
    join audit.evidence_events e on e.event_id = (r.result ->> 'event_id')::uuid
   where r.case_id = p_case.id
     and r.event_type in ('release_co_approved', 'release_approved')
     and (r.result ->> 'version')::integer = p_case.version
     and (r.result ->> 'evidence_seq')::bigint > app.blocked_entry_seq(p_case.id)
$$;

comment on function app.blocked_approvers(app.cases) is
  'Spec 0090: who approved this case at its current version since it last moved into blocked. Only these approvals count toward dismissing a blocked email-only case.';

revoke execute on function app.blocked_entry_seq(uuid), app.blocked_approvers(app.cases) from public;
grant  execute on function app.blocked_entry_seq(uuid), app.blocked_approvers(app.cases) to authenticated, service_role;

-- Guards: 0011's eval_guard, plus the two dismiss guards -------------------------------------

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

-- Transitions --------------------------------------------------------------------------------

insert into app.case_transitions (from_state, event_type, actor_kind, guard_key, to_state, description) values
  ('blocked', 'release_co_approved', 'person', 'email_dismiss_first',     'blocked',   'Email-only case, Ramp unchanged: first of two people dismisses'),
  ('blocked', 'release_approved',    'person', 'email_dismiss_four_eyes', 'releasing', 'Email-only case, Ramp unchanged: second person dismisses; the hold is lifted');

do $$
begin
  if (select count(*) from app.case_transitions where from_state = 'blocked' and actor_kind = 'person') <> 2
     or exists (select 1 from app.case_transitions where from_state = 'blocked' and event_type = 'release_rejected') then
    raise exception 'spec 0090: blocked should have exactly the two dismiss moves for a person, and no reject';
  end if;
end
$$;

-- release_case: 0005's, plus the dismiss from blocked ----------------------------------------

create or replace function app.release_case(
  p_case_id          uuid,
  p_expected_version integer,
  p_action           app.release_action,
  p_reason           text
) returns jsonb
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  v_uid     uuid := auth.uid();
  v_case    app.cases%rowtype;
  v_body    jsonb;
  v_actor   jsonb;
  v_res     jsonb;
  v_dismiss boolean := false;   -- spec 0090: approving a blocked email-only case dismisses it
  v_entry   bigint;             -- spec 0090: where the case's current blocked spell starts
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

  -- Spec 0090: approving a blocked case dismisses it when it is email only. Lock the vendor
  -- first, as change detection and the email door do before they write a change, so no change
  -- for this vendor commits between the check and the move; then read the case again. No key
  -- update is enough to wait for them, and lets foreign-key checks (KEY SHARE) through.
  if v_case.state = 'blocked' and p_action = 'approve' then
    perform 1 from app.vendors v where v.id = v_case.vendor_id for no key update;
    select * into v_case from app.cases c where c.id = p_case_id;
    v_dismiss := coalesce(v_case.state = 'blocked' and app.case_email_only(v_case), false);
  end if;
  if p_action = 'approve' then
    v_entry := app.blocked_entry_seq(p_case_id);
  end if;

  -- A repeated request (a double click, a retried page) returns the first result, whatever
  -- state the case has moved to since. Only this person's own earlier request can match.
  -- Spec 0090: a dismissal is a request of its own. In the dismiss context only the dismiss keys
  -- of the current blocked spell match, so an earlier approval of the release never answers it;
  -- awaiting_release matches exactly the keys it did before; other states match both.
  select r.result into v_res from app.case_event_receipts r
   where r.idempotency_key in (
           case when not v_dismiss then format('%s:%s:v%s:%s', p_case_id, p_action, p_expected_version, v_uid) end,
           case when p_action = 'approve' and not v_dismiss then format('%s:co_approve:v%s:%s', p_case_id, p_expected_version, v_uid) end,
           case when v_entry is not null and v_case.state <> 'awaiting_release'
                then format('%s:dismiss_first:v%s:b%s:%s', p_case_id, p_expected_version, v_entry, v_uid) end,
           case when v_entry is not null and v_case.state <> 'awaiting_release'
                then format('%s:dismiss:v%s:b%s:%s', p_case_id, p_expected_version, v_entry, v_uid) end)
   order by r.applied_at desc
   limit 1;
  if found then
    return v_res || jsonb_build_object('status', 'already_applied');
  end if;
  if not app.has_role(v_uid, 'approver') then
    raise exception 'release_case: approver role required' using errcode = 'insufficient_privilege';
  end if;

  if v_case.version <> p_expected_version then
    raise exception 'release_case: case % changed since you loaded it (now version %)', p_case_id, v_case.version
      using errcode = 'serialization_failure';
  end if;
  if v_case.state <> 'awaiting_release' and not v_dismiss then
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

  -- Spec 0090: a dismissal, always by two people. Only approvals made while the case is blocked
  -- count: with none from someone else this is the first; otherwise the second lifts the hold.
  -- The keys carry the blocked spell, so a later spell at the same version starts afresh.
  if v_dismiss then
    if not exists (select 1 from unnest(app.blocked_approvers(v_case)) a (actor_id) where a.actor_id <> v_uid) then
      v_res := app.apply_case_event(p_case_id, 'release_co_approved', v_actor,
                 format('%s:dismiss_first:v%s:b%s:%s', p_case_id, p_expected_version, v_entry, v_uid), v_body);
      if v_res ->> 'status' = 'already_applied' then
        return v_res;                                    -- the same approver clicking twice
      end if;
      return v_res || jsonb_build_object('status', 'awaiting_second_approver');
    end if;
    return app.apply_case_event(p_case_id, 'release_approved', v_actor,
             format('%s:dismiss:v%s:b%s:%s', p_case_id, p_expected_version, v_entry, v_uid), v_body);
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
  'Four-eyes release with the signed-in user (ADR-9): approver role, not the changer, not the reviewer, version matches; two approvers when the changer is unknown (ADR-10). A blocked email-only case can be approved (dismissed) by two approvers, counting only approvals made while it is blocked (spec 0090); reject from blocked stays refused.';

-- create or replace keeps the earlier grants on eval_guard (owner only) and release_case
-- (authenticated only).
