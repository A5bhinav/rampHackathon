-- app: a bank account Ramp lists as pending approval (spec 0093, D2). Sami.
--
-- A bank change entered in Ramp's dashboard waits for an approver as a pending account, and Dialback
-- opens the case then. Ramp approves it under a new account id, sharing only the last four, so the
-- mirror marks the pending row: a new active account that follows it is its approval, not a second
-- change (D3).

alter table app.bank_accounts
  add column ramp_pending boolean not null default false;

comment on column app.bank_accounts.ramp_pending is
  'Ramp listed this account as pending approval (a change waiting for review); never current (spec 0093).';
