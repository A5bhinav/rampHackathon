"""Give a Supabase user a Dialback profile and roles, or take roles away (specs 0010 and 0027).

The person signs up on the dashboard first (spec 0027), which gives them a profile and the
clerk role, or is created in the Supabase dashboard (Authentication, Users, "Create new user").
Either way they exist in auth.users. Then, with the database's connection string:

    DATABASE_URL=... uv run python scripts/db/add_user.py --email riley@example.com --user-id <id> --name "Riley Approver" --role approver
    DATABASE_URL=... uv run python scripts/db/add_user.py --email riley@example.com --remove-role approver

Roles are clerk, approver, auditor and admin (spec 0095). Approver, auditor and admin need --user-id
as well as the email: anyone can sign up with any address (spec 0027, no email confirmation), so the email
alone doesn't say who holds the account. The person reads their id off the dashboard's header
while signed in, which shows they hold the password. Anyone who can change bank details in Ramp, whatever
their role, also needs --ramp-user-id, so the audit log's actor is matched to them and they
can't approve their own change (R5, ADR-10). Removing a role takes effect on the person's next
request.

The first admin is granted here; after that, admins grant roles on the dashboard's People page.
Every role or Ramp link this script changes is written to the organisation chain with a system actor
(spec 0095, R6), and it refuses what the dashboard refuses: admin on a demo account, and removing
the last admin whose access is on.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg

ROLES = ("clerk", "approver", "auditor", "admin")
NEEDS_USER_ID = ("approver", "auditor", "admin")  # the roles that can release, read as an auditor, or manage people


class UserError(RuntimeError):
    pass


def apply(conn: psycopg.Connection, email: str, name: str | None = None, add: tuple[str, ...] = (),
          remove: tuple[str, ...] = (), ramp_user_id: str | None = None, user_id: str | None = None,
          demo_only: bool = False, reason: str = "changed with scripts/db/add_user.py") -> dict:
    """Upserts the profile, adds and removes roles, and returns the result. One transaction."""
    reason = reason.strip()
    if not reason or len(reason) > 2000:
        raise UserError("--reason must be 1 to 2000 characters: it goes on the organisation chain")
    if conn.execute("select to_regclass('auth.users') is not null").fetchone()[0] is False:
        raise UserError("this database has no auth.users: it isn't a Supabase project (local dev uses seeded users)")
    rows = conn.execute("select id from auth.users where lower(email) = lower(%s)", (email,)).fetchall()
    if not rows:
        raise UserError(f"no Supabase user with the email {email}: they sign up on the dashboard first")
    if len(rows) > 1:  # e.g. an SSO account sharing the address: never guess who gets a role
        raise UserError(f"{len(rows)} Supabase users share the email {email}; grant by hand to the right id")
    if user_id is not None and str(rows[0][0]) != str(user_id).strip().lower():
        raise UserError(f"the account with the email {email} has a different id: check --user-id with the person")
    if user_id is None and any(role in NEEDS_USER_ID for role in add):
        raise UserError("approver, auditor and admin need --user-id: the person reads it off the dashboard's header while signed in")
    user_id = rows[0][0]
    conn.execute("select pg_advisory_xact_lock(hashtext('dialback.admins'))")  # the dashboard's admin lock (spec 0095, D4)
    before = conn.execute("select ramp_user_id from app.user_profiles where user_id = %s", (user_id,)).fetchone()
    held = {r[0] for r in conn.execute("select role::text from app.user_roles where user_id = %s", (user_id,))}
    is_demo = demo_only or conn.execute("select 1 from app.demo_accounts where user_id = %s", (user_id,)).fetchone() is not None
    if is_demo and ("admin" in add or "admin" in held - set(remove)):
        raise UserError("a demo account can't be an admin (spec 0034, spec 0095)")
    if before is None and not name:
        raise UserError("a new Dialback user needs --name")
    if before is None:
        conn.execute("insert into app.user_profiles (user_id, display_name, ramp_user_id) values (%s, %s, %s)",
                     (user_id, name, ramp_user_id))
    elif name or ramp_user_id:
        # A link set here is the team's, so nobody's: the linker is cleared, but only when the link
        # really changes (spec 0095, D5).
        conn.execute(
            "update app.user_profiles set display_name = coalesce(%s, display_name), ramp_user_id = coalesce(%s, ramp_user_id), "
            "ramp_linked_by = case when %s::text is not null and %s::text is distinct from ramp_user_id then null "
            "else ramp_linked_by end where user_id = %s",
            (name, ramp_user_id, ramp_user_id, ramp_user_id, user_id))
    if demo_only:  # spec 0034: this account can act only on demo datasets, and sees only those
        conn.execute("insert into app.demo_accounts (user_id) values (%s) on conflict do nothing", (user_id,))
    events: list[tuple[str, dict]] = []
    for role in add:
        if role not in held:
            conn.execute("insert into app.user_roles (user_id, role) values (%s, %s)", (user_id, role))
            events.append(("role_granted", {"role": role}))
    for role in remove:
        if role in held:
            conn.execute("delete from app.user_roles where user_id = %s and role = %s", (user_id, role))
            events.append(("role_revoked", {"role": role}))
    if "admin" in remove and "admin" in held:
        try:
            conn.execute("select app.admin_keep_one()")
        except psycopg.errors.CheckViolation as exc:
            raise UserError(str(exc).split("\n")[0].removeprefix("admin: ")) from None
    previous = before[0] if before else None
    if ramp_user_id and ramp_user_id != previous:
        events.append(("ramp_link_set", {"ramp_user_id": ramp_user_id, "previous_ramp_user_id": previous}))
    for kind, detail in events:  # R6: the team's changes are on the organisation chain too
        conn.execute("select app.admin_append(null, %s, %s, %s::jsonb, %s)", (kind, user_id, json.dumps(detail), reason))
    profile = conn.execute("select display_name, ramp_user_id from app.user_profiles where user_id = %s", (user_id,)).fetchone()
    roles = [r[0] for r in conn.execute("select role::text from app.user_roles where user_id = %s order by role", (user_id,))]
    demo = conn.execute("select 1 from app.demo_accounts where user_id = %s", (user_id,)).fetchone() is not None
    return {"user_id": str(user_id), "display_name": profile[0], "ramp_user_id": profile[1], "roles": roles,
            "demo_only": demo}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", help="display name, required the first time")
    parser.add_argument("--role", action="append", default=[], choices=ROLES, help="add a role (repeatable)")
    parser.add_argument("--remove-role", action="append", default=[], choices=ROLES, help="remove a role (repeatable)")
    parser.add_argument("--user-id", help="the person's Dialback user id, from the dashboard; required for approver and auditor")
    parser.add_argument("--demo-only", action="store_true",
                        help="a shared one-click demo account: it can act only on demo datasets (spec 0034)")
    parser.add_argument("--ramp-user-id", help="the person's Ramp user id, for anyone who can change bank details in Ramp")
    parser.add_argument("--reason", default="changed with scripts/db/add_user.py",
                        help="why, for the organisation chain (spec 0095)")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(url) as conn:
            result = apply(conn, args.email, args.name, tuple(args.role), tuple(args.remove_role), args.ramp_user_id,
                           args.user_id, args.demo_only, args.reason)
    except UserError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"{result['display_name']} ({result['user_id']}): roles {', '.join(result['roles']) or 'none'}"
          + (f", Ramp user {result['ramp_user_id']}" if result["ramp_user_id"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
