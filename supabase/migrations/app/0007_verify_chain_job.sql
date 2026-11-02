-- app: the scheduled evidence-chain check is a fourth job. Owner: Abhi.
-- Spec: specs/0004-chain-verify-job.md.

alter table app.job_runs drop constraint job_runs_job_check;
alter table app.job_runs
  add constraint job_runs_job_check check (job in ('poll', 'full_sweep', 'reconcile', 'verify_chain'));
