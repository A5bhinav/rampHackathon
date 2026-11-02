-- app: scheduled jobs (the poller, the nightly sweep, reconciliation). Owner: Abhi.
-- Spec: specs/0003-poller-and-reconciliation.md.
--
-- One row per dataset and job. A job is due when its interval has passed on the dataset's
-- clock (app.now), so the lab drives jobs by moving a scenario's clock. A lease keeps two
-- workers from running the same job at once; a crashed worker's lease simply expires.

create table app.job_runs (
  dataset_id       uuid not null references app.datasets (id),
  job              text not null check (job in ('poll', 'full_sweep', 'reconcile')),
  last_started_at  timestamptz,               -- dataset clock
  last_finished_at timestamptz,               -- dataset clock
  last_ok          boolean,
  last_error       text,
  last_result      jsonb not null default '{}',
  watermark        timestamptz,               -- poll only: Ramp's clock, the next poll's from_updated_at
  runs             integer not null default 0,
  locked_by        text,
  locked_until     timestamptz,               -- real clock: leases are about workers, not scenarios
  primary key (dataset_id, job)
);

comment on table app.job_runs is
  'Scheduled jobs per dataset: when each last ran, how it went, the poller''s watermark, and a lease.';

-- Claim a job if it is due and nobody holds it. Returns the row (with its watermark) or nothing.
create function app.claim_job(p_dataset_id uuid, p_job text, p_interval interval, p_worker text, p_lease interval)
returns setof app.job_runs
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  v_now timestamptz := app.now(p_dataset_id);
begin
  insert into app.job_runs (dataset_id, job) values (p_dataset_id, p_job) on conflict do nothing;
  return query
    update app.job_runs j
       set last_started_at = v_now, locked_by = p_worker, locked_until = clock_timestamp() + p_lease, runs = j.runs + 1
     where j.dataset_id = p_dataset_id and j.job = p_job
       and (j.locked_until is null or j.locked_until < clock_timestamp())
       and (j.last_started_at is null or j.last_started_at + p_interval <= v_now)
    returning j.*;
end
$$;

-- Record how a claimed job went and release its lease.
create function app.finish_job(p_dataset_id uuid, p_job text, p_worker text, p_ok boolean, p_error text,
                               p_result jsonb, p_watermark timestamptz)
returns void
language sql
set search_path = pg_catalog, pg_temp
as $$
  update app.job_runs
     set last_finished_at = app.now(p_dataset_id), last_ok = p_ok, last_error = p_error,
         last_result = coalesce(p_result, '{}'::jsonb), watermark = coalesce(p_watermark, watermark),
         locked_by = null, locked_until = null
   where dataset_id = p_dataset_id and job = p_job and locked_by = p_worker
$$;

-- Lock one case row until the caller's transaction ends, and return its state. The service
-- role can't take row locks on app.cases itself (it may only read them; cases change only
-- through apply_case_event), so reconciliation uses this to serialize a re-hold against a
-- release: apply_case_event locks the same row, so whichever comes second waits.
create function app.lock_case(p_case_id uuid) returns text
language sql security definer
set search_path = pg_catalog, pg_temp
as $$
  select c.state::text from app.cases c where c.id = p_case_id for update
$$;

alter table app.job_runs enable row level security;
create policy job_runs_read on app.job_runs for select to authenticated using (true);
revoke all on app.job_runs from public, anon, authenticated, service_role;
grant select on app.job_runs to authenticated, service_role;
grant insert, update on app.job_runs to service_role;

revoke execute on function app.claim_job(uuid, text, interval, text, interval) from public;
revoke execute on function app.finish_job(uuid, text, text, boolean, text, jsonb, timestamptz) from public;
grant execute on function app.claim_job(uuid, text, interval, text, interval) to service_role;
grant execute on function app.finish_job(uuid, text, text, boolean, text, jsonb, timestamptz) to service_role;
revoke execute on function app.lock_case(uuid) from public;
grant execute on function app.lock_case(uuid) to service_role;
