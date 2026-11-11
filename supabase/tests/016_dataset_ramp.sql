-- 016: each dataset's Ramp, the mock or the sandbox, is fixed when it is made (spec 0096, D1).
-- A case can't switch Ramps mid-way, and the one Ramp business the sandbox credentials reach is
-- driven by one current dataset (R1). A demo may be on the sandbox (spec 0103).
begin;
\i supabase/tests/_helpers.sql

create function pg_temp.dataset(p_kind app.dataset_kind, p_label text, p_ramp text default null) returns uuid
language plpgsql as $$
declare
  v_chain uuid;
  v_id    uuid;
begin
  insert into audit.chains (label) values (p_label) returning id into v_chain;
  if p_ramp is null then
    insert into app.datasets (kind, label, chain_id) values (p_kind, p_label, v_chain) returning id into v_id;
  else
    execute 'insert into app.datasets (kind, label, chain_id, ramp) values ($1, $2, $3, $4::app.ramp_target) returning id'
      into v_id using p_kind, p_label, v_chain, p_ramp;
  end if;
  return v_id;
end
$$;

do $$
declare
  d_mock  uuid;
  d_sand  uuid;
  d_next  uuid;
begin
  -- Any current sandbox dataset in this database (a dev run) is set aside first, inside this rolled-back test.
  update app.datasets set retired_at = now() where ramp = 'sandbox' and retired_at is null;

  -- Nothing said: the mock, which is also what every dataset made before this spec reads.
  d_mock := pg_temp.dataset('dev', '016 mock');
  assert (select ramp from app.datasets where id = d_mock) = 'mock', 'a dataset is on the mock unless it says otherwise';
  assert not exists (select 1 from app.datasets where ramp is null), 'no dataset is without a Ramp';

  -- While no sandbox dataset is current, so the one-sandbox rule can't be what refuses these:
  -- a dataset's Ramp is fixed once made.
  perform pg_temp.expect_error(format('update app.datasets set ramp = %L where id = %L', 'sandbox', d_mock), null, '%ramp%');
  -- A demo dataset may be on the sandbox (spec 0103).
  d_next := pg_temp.dataset('demo', '016 demo sandbox', 'sandbox');
  assert (select ramp from app.datasets where id = d_next) = 'sandbox', 'a demo dataset can be on the sandbox';
  update app.datasets set retired_at = now() where id = d_next;
  -- Other columns still change: a dataset can be relabelled or retired.
  update app.datasets set label = '016 mock, relabelled' where id = d_mock;

  -- A dev or lab dataset may be on the sandbox, and stays there.
  d_sand := pg_temp.dataset('dev', '016 sandbox', 'sandbox');
  assert (select ramp from app.datasets where id = d_sand) = 'sandbox', 'a dataset can be on the sandbox';
  perform pg_temp.expect_error(format('update app.datasets set ramp = %L where id = %L', 'mock', d_sand), null, '%ramp%');

  -- One current sandbox dataset: a second is refused while the first is current.
  perform pg_temp.expect_error($q$select pg_temp.dataset('lab', '016 second sandbox', 'sandbox')$q$, '23505');
  -- A retired one doesn't count.
  update app.datasets set retired_at = now() where id = d_sand;
  d_next := pg_temp.dataset('lab', '016 next sandbox', 'sandbox');
  assert (select ramp from app.datasets where id = d_next) = 'sandbox', 'after retiring, a new sandbox dataset can be made';
  -- Bringing the retired one back would make two current sandbox datasets.
  perform pg_temp.expect_error(format('update app.datasets set retired_at = null where id = %L', d_sand), '23505');
end
$$;

rollback;
