-- app: when the worker may next try to fetch an email (spec 0085). Owner: Sami.
-- Null means no wait. A failed fetch sets it from a growing schedule; the fifth failure records outcome 'failed'.
alter table app.inbound_emails add column next_try_at timestamptz;
comment on column app.inbound_emails.next_try_at is 'The fetch is not retried before this time (the dataset clock).';
