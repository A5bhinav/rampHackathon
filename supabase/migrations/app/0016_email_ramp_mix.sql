-- app: an email never joins a case that Ramp changed (specs 0084, 0087; final review finding C1).
-- Owner: Sami (the door's outcomes).
--
-- A case verifies its current change. If an email's change joined a case with a Ramp change, the
-- call would verify the email's account while Ramp pays the clerk's, and two approvers could lift
-- the hold for an account nobody verified (R1). The email door now writes nothing for a vendor
-- whose unfinished case has any change that did not come by email, and lists the email with the
-- outcome ramp_case_open, linked to that case. 0013 is applied, so its check is replaced here.

alter table app.inbound_emails drop constraint inbound_emails_outcome_check;
alter table app.inbound_emails add constraint inbound_emails_outcome_check check (outcome in (
  'pending', 'not_flagged', 'door_off', 'not_demo_vendor', 'no_vendor', 'several_vendors',
  'no_routing_number', 'no_account_number', 'same_as_on_file', 'repeat_request',
  'case_opened', 'joined_case', 'ramp_case_open', 'failed'));
