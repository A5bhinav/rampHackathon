"""The admin role over the API (spec 0095): an admin holds every role's powers but R5 still binds
them, people are managed only through the database's doors, a turned-off person is refused
everywhere, and the demo commands run only after the admin check is on the organisation chain."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
from conftest import core_settings, make_world
from contract import _retrieve, assert_matches
from dialback_core.app import create_app
from dialback_core.db import system_tx
from dialback_core.detection import import_all
from dialback_core.flags import RampMode
from flows import case_of, change_and_detect, report
from jsonschema import Draft202012Validator
from referencing import Registry
from test_api import token

pytestmark = pytest.mark.anyio

SEED = Path(__file__).resolve().parents[3] / "scripts" / "demo" / "seed.json"


DATABASE_URL: str = ""


async def add_person(pool, name: str, *roles: str) -> uuid.UUID:
    """As the table owner, the way add_user.py does: the server role may not grant admin (spec 0095)."""
    uid = uuid.uuid4()
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as owner:
        await owner.execute("insert into app.user_profiles (user_id, display_name) values (%s, %s)", (uid, name))
        for role in roles:
            await owner.execute("insert into app.user_roles (user_id, role) values (%s, %s)", (uid, role))
    return uid


@pytest.fixture(autouse=True)
def _owner_url(database_url):
    global DATABASE_URL
    DATABASE_URL = database_url


@pytest.fixture
async def admin_world(pool, database_url):
    """The usual in-process core and mock, plus an admin, with the core's demo commands wired to
    the mock and the test database as their owner connection."""
    settings = core_settings(core_public_url="http://core")
    w = await make_world(pool, settings=settings)
    app = create_app(settings, pool=pool, connector=w.connector, demo_mock=w.mock, owner_url=database_url)
    app.state.demo_seed = SEED
    w.admin_api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core")
    w.users["admin"] = await add_person(pool, "Ada Admin", "admin")
    yield w
    await w.admin_api.aclose()
    await w.core.aclose()
    await w.mock.aclose()


async def call(w, method: str, url: str, who: str, spec: str, status: int = 200, body: dict | None = None):
    r = await w.admin_api.request(method, url, headers=token(w.users[who]), json=body)
    assert r.status_code == status, r.text
    assert_matches(spec, method.lower(), status, r.json())
    return r.json()


async def verified_case(w, actor="clerk"):
    await import_all(w.pool, w.ramp, w.keys, w.dataset.id)
    await change_and_detect(w, actor=actor)
    case = await case_of(w)
    await report(w, case["id"], w.voice.placed[-1]["call_id"])
    await w.worker.drain(w.dataset.id)
    return await case_of(w)


async def test_an_admin_holds_every_role_and_releases_someone_elses_change(admin_world):
    w = admin_world
    me = await call(w, "GET", "/api/me", "admin", "/api/me")
    assert me["roles"] == ["admin"], "the roles held, not the ones admin implies"
    case = await verified_case(w)
    detail = await call(w, "GET", f"/api/cases/{case['id']}", "admin", "/api/cases/{case_id}")
    assert detail["permissions"]["can_approve"] and detail["permissions"]["can_reject"]
    r = await call(w, "POST", f"/api/cases/{case['id']}/release", "admin", "/api/cases/{case_id}/release",
                   body={"expected_version": case["version"], "action": "approve", "reason": "Checked the call."})
    assert r["status"] == "applied"


async def test_the_admin_who_linked_the_clerk_cant_release_the_clerks_change(admin_world):
    """D5: linking someone to a Ramp user makes you a changer of what they change through it (R5)."""
    w = admin_world
    clerk, ramp_clerk = w.users["clerk"], w.refs["clerk"]
    await call(w, "POST", f"/api/people/{clerk}/ramp-link", "admin", "/api/people/{user_id}/ramp-link",
               body={"ramp_user_id": None, "reason": "Relinking."})
    await call(w, "POST", f"/api/people/{clerk}/ramp-link", "admin", "/api/people/{user_id}/ramp-link",
               body={"ramp_user_id": ramp_clerk, "reason": "Pat enters changes in Ramp."})
    case = await verified_case(w)
    assert w.users["admin"] in case["changer_ids"] and clerk in case["changer_ids"]
    detail = await call(w, "GET", f"/api/cases/{case['id']}", "admin", "/api/cases/{case_id}")
    assert not detail["permissions"]["can_approve"]
    assert any("(R5)" in r for r in detail["permissions"]["reasons"])
    r = await call(w, "POST", f"/api/cases/{case['id']}/release", "admin", "/api/cases/{case_id}/release", status=403,
                   body={"expected_version": case["version"], "action": "approve", "reason": "Mine."})
    assert "R5" in r["message"]
    await call(w, "POST", f"/api/cases/{case['id']}/release", "approver1", "/api/cases/{case_id}/release",
               body={"expected_version": case["version"], "action": "approve", "reason": "Checked the call."})


async def test_people_are_managed_through_the_doors_and_refusals_map_to_the_contract(admin_world):
    w = admin_world
    people = await call(w, "GET", "/api/people", "admin", "/api/people")
    ids = {p["id"] for p in people["people"]}
    assert {str(w.users["admin"]), str(w.users["clerk2"])} <= ids
    for who in ("clerk", "approver1"):
        r = await call(w, "GET", "/api/people", who, "/api/people", status=403)
        assert r["code"] == "forbidden"
    clerk2 = w.users["clerk2"]
    p = await call(w, "POST", f"/api/people/{clerk2}/roles", "admin", "/api/people/{user_id}/roles",
                   body={"role": "approver", "grant": True, "reason": "Covers releases this week."})
    assert p["roles"] == ["clerk", "approver"]
    r = await call(w, "POST", f"/api/people/{clerk2}/roles", "admin", "/api/people/{user_id}/roles", status=409,
                   body={"role": "approver", "grant": True, "reason": "Again."})
    assert r["code"] == "transition_refused" and "already" in r["message"]
    r = await call(w, "POST", f"/api/people/{uuid.uuid4()}/roles", "admin", "/api/people/{user_id}/roles", status=404,
                   body={"role": "approver", "grant": True, "reason": "Who?"})
    r = await w.admin_api.post(f"/api/people/{clerk2}/roles", headers=token(w.users["admin"]),
                               json={"role": "approver", "grant": False, "reason": ""})
    assert r.status_code == 422, "a reason is required"
    r = await call(w, "POST", f"/api/people/{w.users['admin']}/ramp-link", "admin", "/api/people/{user_id}/ramp-link",
                   status=409, body={"ramp_user_id": "ramp-self", "reason": "Me."})
    assert "your own" in r["message"]
    r = await call(w, "POST", f"/api/people/{clerk2}/roles", "approver1", "/api/people/{user_id}/roles", status=403,
                   body={"role": "admin", "grant": True, "reason": "Let me in."})

    log = await call(w, "GET", "/api/admin/log", "admin", "/api/admin/log")
    assert log["chain"]["ok"]
    assert log["events"][0]["type"] == "role_granted" and log["events"][0]["subject"]["id"] == str(clerk2)
    assert log["events"][0]["actor"]["display_name"] == "Ada Admin"


async def test_a_turned_off_person_is_refused_on_every_request(admin_world):
    w = admin_world
    off = w.users["approver2"]
    await call(w, "POST", f"/api/people/{off}/access", "admin", "/api/people/{user_id}/access",
               body={"enabled": False, "reason": "On leave."})
    for url in ("/api/me", "/api/datasets", f"/api/cases?dataset_id={w.dataset.id}"):
        r = await w.admin_api.get(url, headers=token(off))
        assert r.status_code == 403 and r.json() == {"code": "forbidden", "message": "Your access is turned off. Ask an admin."}
    p = await call(w, "POST", f"/api/people/{off}/access", "admin", "/api/people/{user_id}/access",
                   body={"enabled": True, "reason": "Back."})
    assert p["access_enabled"] and p["roles"] == ["approver"], "roles kept"
    assert (await w.admin_api.get("/api/me", headers=token(off))).status_code == 200


async def test_two_admins_demoting_each_other_leave_one(admin_world, database_url):
    """A5/A16: the doors take one lock and check the caller after it, so this can't race to zero."""
    w = admin_world
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as owner:
        await owner.execute("delete from app.user_roles where role = 'admin'")  # only these two, for this test
    a, b = await add_person(w.pool, "Admin A", "admin"), await add_person(w.pool, "Admin B", "admin")

    async def demote(actor, target):
        async with await psycopg.AsyncConnection.connect(database_url) as conn:
            await conn.execute("set role authenticated")
            await conn.execute("select set_config('request.jwt.claims', %s, false)",
                               (json.dumps({"sub": str(actor), "role": "authenticated"}),))
            try:
                await conn.execute("select app.admin_set_role(%s, 'admin', false, 'Race.')", (target,))
                await asyncio.sleep(0.2)  # hold the lock while the other waits
                await conn.commit()
                return "applied"
            except psycopg.errors.Error as exc:
                await conn.rollback()
                return type(exc).__name__

    results = sorted(await asyncio.gather(demote(a, b), demote(b, a)))
    assert results in (["InsufficientPrivilege", "applied"], ["CheckViolation", "applied"]), results
    async with system_tx(w.pool) as conn:
        left = await (await conn.execute("select count(*) as n from app.user_roles where role = 'admin'")).fetchone()
    assert left["n"] == 1


async def test_demo_commands_need_an_admin_and_log_their_intent_first(admin_world):
    w = admin_world
    before = await call(w, "GET", "/api/admin/log", "admin", "/api/admin/log")
    r = await call(w, "POST", "/api/admin/demo/reset", "approver1", "/api/admin/demo/reset", status=403,
                   body={"reason": "Let me."})
    assert (await call(w, "GET", "/api/admin/log", "admin", "/api/admin/log"))["events"] == before["events"], "nothing logged"

    state = await call(w, "POST", "/api/admin/demo/reset", "admin", "/api/admin/demo/reset",
                       body={"link_clerk_user_id": str(w.users["clerk2"]), "reason": "Fresh demo."})
    assert state["chaos"] is False and state["chain"]["ok"]
    assert not any("'clerk'" in x for x in state["warnings"]), "the clerk is linked"
    linked = await call(w, "GET", "/api/people", "admin", "/api/people")
    assert next(p for p in linked["people"] if p["id"] == str(w.users["clerk2"]))["ramp_user_id"] is not None

    on = await call(w, "POST", "/api/admin/demo/chaos", "admin", "/api/admin/demo/chaos",
                    body={"enabled": True, "reason": "Show a failed hold."})
    assert on["chaos"] is True
    off = await call(w, "POST", "/api/admin/demo/chaos", "admin", "/api/admin/demo/chaos",
                     body={"enabled": False, "reason": "Back to normal."})
    assert off["chaos"] is False
    r = await w.admin_api.post("/api/admin/demo/tamper", headers=token(w.users["admin"]),
                               json={"confirm": "yes", "reason": "Oops."})
    assert r.status_code == 422, "the typed confirmation is required"

    log = await call(w, "GET", "/api/admin/log", "admin", "/api/admin/log", body=None)
    kinds = [e["type"] for e in log["events"][:4]]
    assert kinds == ["demo_chaos", "demo_chaos", "ramp_link_set", "demo_reset"], kinds

    r = await call(w, "POST", "/api/admin/demo/reset", "admin", "/api/admin/demo/reset", status=409,
                   body={"link_clerk_user_id": str(w.users["admin"]), "reason": "Me as the clerk."})
    assert "yourself" in r["message"]


async def test_demo_commands_refuse_the_sandbox(pool, database_url):
    settings = core_settings(ramp_mode=RampMode.SANDBOX)
    w = await make_world(pool, settings=core_settings())
    app = create_app(settings, pool=pool, connector=w.connector, demo_mock=w.mock, owner_url=database_url)
    app.state.demo_seed = SEED
    api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core")
    admin = await add_person(pool, "Sandbox Admin", "admin")
    try:
        r = await api.post("/api/admin/demo/reset", headers=token(admin), json={"reason": "Reset."})
        assert r.status_code == 409 and "sandbox" in r.json()["message"]
        assert_matches("/api/admin/demo/reset", "post", 409, r.json())
    finally:
        await api.aclose()
        await w.core.aclose()
        await w.mock.aclose()


async def test_every_org_chain_envelope_matches_the_admin_event_schema(admin_world):
    """A8: what the doors and the server's demo calls wrote is exactly admin-event.schema.json."""
    w = admin_world
    await call(w, "POST", f"/api/people/{w.users['clerk2']}/roles", "admin", "/api/people/{user_id}/roles",
               body={"role": "auditor", "grant": True, "reason": "Year-end."})
    await call(w, "POST", "/api/admin/demo/reset", "admin", "/api/admin/demo/reset", body={"reason": "Fresh."})
    schema = json.loads((Path(__file__).resolve().parents[3] / "packages/contracts/schemas/admin-event.schema.json").read_text())
    validator = Draft202012Validator(schema, registry=Registry(retrieve=_retrieve))
    async with system_tx(w.pool) as conn:
        rows = await (await conn.execute(
            "select e.body_jcs from audit.evidence_events e join app.org_settings o on o.chain_id = e.chain_id")).fetchall()
    assert rows, "the org chain has events"
    for r in rows:
        errors = [e.message for e in validator.iter_errors(json.loads(r["body_jcs"]))]
        assert not errors, (r["body_jcs"], errors)
