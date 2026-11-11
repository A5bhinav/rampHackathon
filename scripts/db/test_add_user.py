"""scripts/db/add_user.py against a migrated throwaway database with a stand-in auth.users (specs 0010 and 0027).

    uv run --with pytest pytest scripts/db/test_add_user.py
"""
from __future__ import annotations

import uuid

import psycopg
import pytest
from add_user import UserError, apply
from pgtemp import database_url  # noqa: F401  (the fixture)


@pytest.fixture
def conn(database_url):  # noqa: F811
    with psycopg.connect(database_url) as c:
        c.execute("create schema if not exists auth")
        c.execute("create table if not exists auth.users (id uuid primary key, email text)")
        yield c
        c.rollback()  # nothing a test does survives it


def existing(conn, email: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))
    return user_id


def test_a_created_person_gets_a_profile_and_roles_and_loses_them_again(conn):
    user_id = existing(conn, "riley@example.test")
    result = apply(conn, "Riley@Example.TEST", name="Riley Approver", add=("approver", "auditor"), user_id=str(user_id))
    assert result == {"user_id": str(user_id), "display_name": "Riley Approver", "ramp_user_id": None, "roles": ["approver", "auditor"],
                      "demo_only": False}
    again = apply(conn, "riley@example.test", add=("approver",), user_id=str(user_id).upper())  # idempotent, and the name stays
    assert again["roles"] == ["approver", "auditor"] and again["display_name"] == "Riley Approver"
    assert apply(conn, "riley@example.test", remove=("approver",))["roles"] == ["auditor"]


def test_a_clerk_is_matched_to_their_ramp_user(conn):
    existing(conn, "pat@example.test")
    result = apply(conn, "pat@example.test", name="Pat Clerk", add=("clerk",), ramp_user_id="ramp-user-123")
    assert result["ramp_user_id"] == "ramp-user-123" and result["roles"] == ["clerk"]


def test_a_demo_account_is_marked_and_stays_marked(conn):
    """Spec 0034: --demo-only puts the account in app.demo_accounts, once."""
    user_id = existing(conn, "demo.approver@example.test")
    result = apply(conn, "demo.approver@example.test", name="Demo Approver", add=("approver",), user_id=str(user_id), demo_only=True)
    assert result["demo_only"] is True and result["roles"] == ["approver"]
    assert apply(conn, "demo.approver@example.test", demo_only=True)["demo_only"] is True
    assert conn.execute("select count(*) from app.demo_accounts where user_id = %s", (user_id,)).fetchone()[0] == 1


@pytest.mark.parametrize(("email", "name", "message"), [
    ("nobody@example.test", "Nobody", "sign up on the dashboard first"),
    ("new@example.test", None, "needs --name"),
    ("shared@example.test", "Shared", "share the email"),
])
def test_refuses_people_who_dont_exist_are_unnamed_or_ambiguous(conn, email, name, message):
    if email != "nobody@example.test":
        existing(conn, email)
    if email == "shared@example.test":
        existing(conn, "SHARED@example.test")
    with pytest.raises(UserError, match=message):
        apply(conn, email, name=name, add=("clerk",))


def test_approver_and_auditor_need_the_accounts_own_id(conn):
    """Anyone can sign up with any address (spec 0027), so the email alone doesn't say who holds
    the account: the id the person reads off their dashboard does."""
    user_id = existing(conn, "judge@example.test")
    for role in ("approver", "auditor"):
        with pytest.raises(UserError, match="need --user-id"):
            apply(conn, "judge@example.test", name="Judge", add=(role,))
    with pytest.raises(UserError, match="different id"):
        apply(conn, "judge@example.test", name="Judge", add=("approver",), user_id=str(uuid.uuid4()))
    roles = conn.execute("select count(*) from app.user_roles where user_id = %s", (user_id,)).fetchone()[0]
    assert roles == 0, "a refused grant grants nothing"
    assert apply(conn, "judge@example.test", name="Judge", add=("clerk",))["roles"] == ["clerk"], "clerk needs no id"
    assert apply(conn, "judge@example.test", add=("approver",), user_id=str(user_id))["roles"] == ["approver", "clerk"]
    assert apply(conn, "judge@example.test", remove=("approver",))["roles"] == ["clerk"], "removing needs no id"


def test_admin_needs_the_accounts_own_id_and_is_never_a_demo_account(conn):
    """Spec 0095: the first admin is granted here, by id like approver; a demo account can't hold admin."""
    user_id = existing(conn, "ada@example.test")
    with pytest.raises(UserError, match="need --user-id"):
        apply(conn, "ada@example.test", name="Ada Admin", add=("admin",))
    assert apply(conn, "ada@example.test", name="Ada Admin", add=("admin",), user_id=str(user_id))["roles"] == ["admin"]
    judge = existing(conn, "judge.admin@example.test")
    with pytest.raises(UserError, match="demo account can't be an admin"):
        apply(conn, "judge.admin@example.test", name="Judge", add=("admin",), user_id=str(judge), demo_only=True)
    apply(conn, "judge.admin@example.test", name="Judge", add=("approver",), user_id=str(judge), demo_only=True)
    with pytest.raises(UserError, match="demo account can't be an admin"):
        apply(conn, "judge.admin@example.test", add=("admin",), user_id=str(judge))
    assert conn.execute("select count(*) from app.user_roles where user_id = %s and role = 'admin'", (judge,)).fetchone()[0] == 0
    with pytest.raises(UserError, match="demo account can't be an admin"):
        apply(conn, "ada@example.test", demo_only=True)


def test_the_teams_changes_are_on_the_org_chain_and_keep_an_admin(conn):
    """Spec 0095, R6: what the script changes is logged with a system actor; no-ops log nothing."""
    user_id = existing(conn, "first.admin@example.test")
    before = conn.execute("select count(*) from audit.evidence_events e join app.org_settings o on o.chain_id = e.chain_id").fetchone()[0]
    apply(conn, "first.admin@example.test", name="First Admin", add=("admin",), user_id=str(user_id), ramp_user_id="ramp-1",
          reason="The first admin.")
    apply(conn, "first.admin@example.test", add=("admin",), user_id=str(user_id))  # already held: nothing logged
    rows = conn.execute("select type, actor_kind, actor_id, body_jcs::jsonb from audit.evidence_events e "
                        "join app.org_settings o on o.chain_id = e.chain_id order by seq").fetchall()[before:]
    assert [(r[0], r[1], r[2]) for r in rows] == [("role_granted", "system", None), ("ramp_link_set", "system", None)]
    assert rows[0][3]["detail"] == {"role": "admin"} and rows[0][3]["reason"] == "The first admin."
    assert rows[0][3]["subject_user_id"] == str(user_id)
    with pytest.raises(UserError, match="last admin"):
        apply(conn, "first.admin@example.test", remove=("admin",))


def test_the_linker_is_cleared_only_when_the_team_changes_the_link(conn):
    """Spec 0095, D5: re-running the script with the same Ramp id keeps whoever linked it a changer."""
    user_id = existing(conn, "linked@example.test")
    apply(conn, "linked@example.test", name="Linked Clerk", add=("clerk",), ramp_user_id="ramp-a")
    admin = uuid.uuid4()
    conn.execute("update app.user_profiles set ramp_linked_by = %s where user_id = %s", (admin, user_id))
    apply(conn, "linked@example.test", ramp_user_id="ramp-a")
    assert conn.execute("select ramp_linked_by from app.user_profiles where user_id = %s", (user_id,)).fetchone()[0] == admin
    apply(conn, "linked@example.test", ramp_user_id="ramp-b")
    assert conn.execute("select ramp_linked_by from app.user_profiles where user_id = %s", (user_id,)).fetchone()[0] is None
    with pytest.raises(UserError, match="--reason"):
        apply(conn, "linked@example.test", add=("auditor",), user_id=str(user_id), reason="  ")
