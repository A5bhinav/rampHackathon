-- app: the scheduled evidence-chain check is a fourth job. Owner: Abhi.
-- Spec: specs/0004-chain-verify-job.md.

alter table app.job_runs drop constraint job_runs_job_check;
alter table app.job_runs
  add constraint job_runs_job_check check (job in ('poll', 'full_sweep', 'reconcile', 'verify_chain'));

-- One open "chain broken" alert per dataset, even if two checks race (an expired lease).
create unique index alerts_one_open_chain_broken on app.alerts (dataset_id)
  where kind = 'chain_broken' and resolved_at is null;
