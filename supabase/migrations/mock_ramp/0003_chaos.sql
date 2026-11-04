-- mock_ramp: chaos mode and the release-side failure switches (spec 0008). Owner: Abhi.
--
-- Chaos mode makes Ramp's API unreliable on purpose, with a seed so a run can be repeated: a
-- share of requests answers 5xx (half of them after the work was done, a lost response), every
-- request can be slow, and a share of webhook deliveries is dropped. The release-side switches
-- stay on until turned off, like fail_holds.

alter table mock_ramp.settings
  add column fail_releases        boolean not null default false,
  add column ignore_releases      boolean not null default false,
  add column fail_bill_updates    boolean not null default false,
  add column chaos_seed           integer,
  add column chaos_error_permille integer not null default 0 check (chaos_error_permille between 0 and 1000),
  add column chaos_max_latency_ms integer not null default 0 check (chaos_max_latency_ms between 0 and 10000),
  add column chaos_drop_permille  integer not null default 0 check (chaos_drop_permille between 0 and 1000);

comment on column mock_ramp.settings.fail_releases is 'While true, every release request answers 500 (the endpoint is down).';
comment on column mock_ramp.settings.ignore_releases is 'While true, a release answers 200 but the hold stays (the read-back must catch it).';
comment on column mock_ramp.settings.fail_bill_updates is 'While true, updating a bill''s payment date answers 500.';
comment on column mock_ramp.settings.chaos_seed is 'Chaos mode is on while this is set; the same seed repeats the same faults.';

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
    when 'fail_releases' then
      select s.fail_releases into v_was from mock_ramp.settings s where s.business_id = p_business_id;
    when 'ignore_releases' then
      select s.ignore_releases into v_was from mock_ramp.settings s where s.business_id = p_business_id;
    when 'fail_bill_updates' then
      select s.fail_bill_updates into v_was from mock_ramp.settings s where s.business_id = p_business_id;
    else
      raise exception 'mock_ramp: unknown switch %', p_switch;
  end case;
  return coalesce(v_was, false);
end
$$;
