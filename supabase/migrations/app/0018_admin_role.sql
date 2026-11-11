-- app: the admin role (spec 0095). Owner: Abhi.
--
-- Only the enum value. Postgres refuses to use an enum value in the transaction that added it,
-- and every migration runs as one transaction, so 0019 uses it.
alter type app.user_role add value if not exists 'admin';
