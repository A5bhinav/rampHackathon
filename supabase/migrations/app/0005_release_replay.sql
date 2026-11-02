-- app: a repeated release request returns its first result. Owner: Abhi.
--
-- release_case checked the case's state before looking for an earlier receipt, so an
-- approver's double click after a successful approval was refused as "not awaiting_release"
-- instead of answering "already applied". The dashboard contract promises the latter.
-- Found by the core's API tests (apps/core/tests/test_api.py).

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

  -- A repeated request (a double click, a retried page) returns the first result, whatever
  -- state the case has moved to since. Only this person's own earlier request can match.
  select r.result into v_res from app.case_event_receipts r
   where r.idempotency_key in (
           format('%s:%s:v%s:%s', p_case_id, p_action, p_expected_version, v_uid),
           case when p_action = 'approve' then format('%s:co_approve:v%s:%s', p_case_id, p_expected_version, v_uid) end)
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

-- create or replace keeps the earlier grants (authenticated only).
