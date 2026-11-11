-- app: stored inbound emails and the per-dataset email door switch (spec 0084). Owner: Sami.
--
-- A bank-change email can open a Dialback case on its own, but only on a demo dataset and only
-- for the vendors listed here. An empty list means the door is off for that dataset. The email
-- body is never stored (only its sha256), and a full account number never is either (ADR-8):
-- just the routing number and the last four digits.

alter table app.datasets add column email_door_vendors uuid[] not null default '{}';
comment on column app.datasets.email_door_vendors is
  'Vendor ids whose emails the door may act on. Empty means the door is off for this dataset.';

create table app.inbound_emails (
  id               bigint generated always as identity primary key,
  dataset_id       uuid not null references app.datasets (id),
  delivery_id      bigint references app.inbound_webhooks (id),
  resend_email_id  text not null,
  message_id       text,
  thread_id        text,
  sender           text not null,
  sender_domain    text,
  subject          text,
  received_at      timestamptz not null,
  raw_sha256       text,                     -- null until the email has been fetched
  has_attachments  boolean not null,
  flagged          boolean not null default false,
  outcome          text not null default 'pending' check (outcome in (
                     'pending', 'not_flagged', 'door_off', 'not_demo_vendor', 'no_vendor', 'several_vendors',
                     'no_routing_number', 'no_account_number', 'same_as_on_file', 'repeat_request',
                     'case_opened', 'joined_case', 'failed')),
  reply            text not null default 'not_flagged' check (reply in (
                     'not_flagged', 'sent', 'not_configured', 'unauthenticated', 'suppressed',
                     'rate_limited', 'failed')),
  vendor_id        uuid,
  case_id          uuid,
  change_event_id  uuid,
  routing_number   text,
  last4            text check (last4 ~ '^[0-9]{4}$'),
  red_flags        text[] not null default '{}',
  processed_at     timestamptz,
  tries            int not null default 0,   -- fetch attempts so far
  error            text,
  unique (dataset_id, resend_email_id)       -- a redelivered webhook finds its row instead of making a second
);
comment on table app.inbound_emails is
  'One row per email the door received. Holds the sha256 of the raw email, never its body or a full account number.';

-- Everyone signed in can read (the dashboard); only the service role writes, as for app.alerts.
-- The restrictive policy mirrors cases_demo_accounts_see_demo (0012): a demo account sees only
-- the emails of demo datasets.
alter table app.inbound_emails enable row level security;
create policy inbound_emails_read on app.inbound_emails for select to authenticated using (true);
create policy inbound_emails_demo_accounts_see_demo on app.inbound_emails as restrictive for select to authenticated
  using (not (select app.is_demo_account(auth.uid()))
         or exists (select 1 from app.datasets d where d.id = app.inbound_emails.dataset_id and d.kind = 'demo'));

revoke all on app.inbound_emails from public, anon, authenticated, service_role;
grant select on app.inbound_emails to authenticated, service_role;
grant insert, update on app.inbound_emails to service_role;
grant usage, select on sequence app.inbound_emails_id_seq to service_role;
