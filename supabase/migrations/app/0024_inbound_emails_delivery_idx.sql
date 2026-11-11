-- app: an index on inbound_emails.delivery_id (spec 0084; final review minor M10). Owner: Sami.
--
-- The inbox's claim joins each stored Resend notice to its email row by delivery_id, once per claim,
-- and the column is a foreign key to app.inbound_webhooks. 0020 is applied, so the index is added here.
create index inbound_emails_delivery_idx on app.inbound_emails (delivery_id);
