-- mock_ramp: a persistent "hold endpoint is down" switch. Owner: Abhi.
--
-- fail_next_hold fails one request, which the core's HTTP adapter absorbs with its own retry.
-- To exercise the path where Ramp keeps refusing holds (the case goes to a person with
-- "Hold not confirmed"), the demo smoke run and the attack lab need a switch that stays on
-- until it is turned off.

alter table mock_ramp.settings add column fail_holds boolean not null default false;

comment on column mock_ramp.settings.fail_holds is
  'While true, every hold request answers 500 (Ramp''s hold endpoint is down). Not consumed.';

create or replace function mock_ramp.consume_switch(p_business_id uuid, p_switch text) returns boolean
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  v_was boolean;
begin
  case p_switch
    when 'fail_next_hold' then
      update mock_ramp.settings set fail_next_hold = false, updated_at = now()
       where business_id = p_business_id and fail_next_hold returning true into v_was;
    when 'fail_next_release' then
      update mock_ramp.settings set fail_next_release = false, updated_at = now()
       where business_id = p_business_id and fail_next_release returning true into v_was;
    when 'fail_holds' then
      select s.fail_holds into v_was from mock_ramp.settings s where s.business_id = p_business_id;
    else
      raise exception 'mock_ramp: unknown switch %', p_switch;
  end case;
  return coalesce(v_was, false);
end
$$;
