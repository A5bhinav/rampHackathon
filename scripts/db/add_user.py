"""Give a Supabase user a Dialback profile and roles, or take roles away (specs 0010 and 0027).

The person signs up on the dashboard first (spec 0027), which gives them a profile and the
clerk role, or is created in the Supabase dashboard (Authentication, Users, "Create new user").
Either way they exist in auth.users. Then, with the database's connection string:

    DATABASE_URL=... uv run python scripts/db/add_user.py --email riley@example.com --user-id <id> --name "Riley Approver" --role approver
    DATABASE_URL=... uv run python scripts/db/add_user.py --email riley@example.com --remove-role approver

Roles are clerk, approver and auditor. Approver and auditor need --user-id as well as the
email: anyone can sign up with any address (spec 0027, no email confirmation), so the email
alone doesn't say who holds the account. The person reads their id off the dashboard's header
while signed in, which shows they hold the password. Anyone who can change bank details in Ramp, whatever
their role, also needs --ramp-user-id, so the audit log's actor is matched to them and they
can't approve their own change (R5, ADR-10). Removing a role takes effect on the person's next
request.
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg

ROLES = ("clerk", "approver", "auditor")
NEEDS_USER_ID = ("approver", "auditor")  # the roles that can release, or read as an auditor


class UserError(RuntimeError):
    pass


def apply(conn: psycopg.Connection, email: str, name: str | None = None, add: tuple[str, ...] = (),
          remove: tuple[str, ...] = (), ramp_user_id: str | None = None, user_id: str | None = None,
          demo_only: bool = False) -> dict:
    """Upserts the profile, adds and removes roles, and returns the result. One transaction."""
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
        raise UserError("approver and auditor need --user-id: the person reads it off the dashboard's header while signed in")
    user_id = rows[0][0]
    has_profile = conn.execute("select 1 from app.user_profiles where user_id = %s", (user_id,)).fetchone() is not None
    if not has_profile and not name:
        raise UserError("a new Dialback user needs --name")
    if name or ramp_user_id or not has_profile:
        conn.execute(
            "insert into app.user_profiles (user_id, display_name, ramp_user_id) values (%s, %s, %s) "
            "on conflict (user_id) do update set display_name = coalesce(%s, app.user_profiles.display_name), "
            "ramp_user_id = coalesce(%s, app.user_profiles.ramp_user_id)",
            (user_id, name, ramp_user_id, name, ramp_user_id))
    if demo_only:  # spec 0034: this account can act only on demo datasets, and sees only those
        conn.execute("insert into app.demo_accounts (user_id) values (%s) on conflict do nothing", (user_id,))
    for role in add:
        conn.execute("insert into app.user_roles (user_id, role) values (%s, %s) on conflict do nothing", (user_id, role))
    for role in remove:
        conn.execute("delete from app.user_roles where user_id = %s and role = %s", (user_id, role))
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
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(url) as conn:
            result = apply(conn, args.email, args.name, tuple(args.role), tuple(args.remove_role), args.ramp_user_id,
                           args.user_id, args.demo_only)
    except UserError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"{result['display_name']} ({result['user_id']}): roles {', '.join(result['roles']) or 'none'}"
          + (f", Ramp user {result['ramp_user_id']}" if result["ramp_user_id"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
