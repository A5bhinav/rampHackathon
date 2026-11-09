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
    assert result == {"user_id": str(user_id), "display_name": "Riley Approver", "ramp_user_id": None, "roles": ["approver", "auditor"]}
    again = apply(conn, "riley@example.test", add=("approver",), user_id=str(user_id).upper())  # idempotent, and the name stays
    assert again["roles"] == ["approver", "auditor"] and again["display_name"] == "Riley Approver"
    assert apply(conn, "riley@example.test", remove=("approver",))["roles"] == ["auditor"]


def test_a_clerk_is_matched_to_their_ramp_user(conn):
    existing(conn, "pat@example.test")
    result = apply(conn, "pat@example.test", name="Pat Clerk", add=("clerk",), ramp_user_id="ramp-user-123")
    assert result["ramp_user_id"] == "ramp-user-123" and result["roles"] == ["clerk"]


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
