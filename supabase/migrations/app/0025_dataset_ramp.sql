-- app: each dataset's Ramp, the mock or Ramp's sandbox, fixed when it is made (spec 0096, D1). Sami.
--
-- Every dataset made before this reads 'mock', which is what all of them use. A demo dataset is
-- never on the sandbox (spec 0034). The sandbox credentials reach one Ramp business, so one current
-- dataset at a time drives it: two would place and lift holds on the same vendors (R1). And a
-- dataset can't switch Ramps, since its vendors, bills and holds live in the one it was made on.

create type app.ramp_target as enum ('mock', 'sandbox');

alter table app.datasets
  add column ramp app.ramp_target not null default 'mock',
  add constraint datasets_demo_on_the_mock check (kind <> 'demo' or ramp = 'mock');

create unique index datasets_one_current_sandbox on app.datasets ((true))
  where ramp = 'sandbox' and retired_at is null;

create function app.keep_dataset_ramp() returns trigger
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
begin
  if new.ramp is distinct from old.ramp then
    raise exception 'a dataset''s ramp is fixed when it is made (spec 0096): make a new dataset instead'
      using errcode = 'check_violation';
  end if;
  return new;
end
$$;

create trigger datasets_keep_ramp before update of ramp on app.datasets
  for each row execute function app.keep_dataset_ramp();
