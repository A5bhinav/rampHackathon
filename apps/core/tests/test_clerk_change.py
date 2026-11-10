"""A clerk's bank change from the dashboard (spec 0077): demo datasets and the Ramp mock only, made
as the clerk's linked Ramp user, then the real pipeline. Every refusal sends nothing to Ramp, and the
full account number is never stored, logged or returned (ADR-8)."""
from __future__ import annotations

import dataclasses
import logging
import uuid

import pytest
from conftest import make_world
from contract import assert_matches
from dialback_core.db import system_tx
from dialback_core.detection import import_all
from dialback_core.flags import RampMode
from flows import case_of
from psycopg.types.json import Jsonb
from test_api import get, token

pytestmark = pytest.mark.anyio

ROUTING = "026009593"            # a valid ABA routing number (Bank of America's, fictional account)
ACCOUNT = "987654321098"         # the full number the clerk types; must never be stored or returned
SPEC = "/api/datasets/{dataset_id}/vendors/{vendor_id}/bank-change"


@pytest.fixture
async def demo(pool):
    """A current demo dataset whose clerk is linked to the mock business's seeded clerk."""
    w = await make_world(pool, kind="demo")
    async with system_tx(pool) as conn:
        await conn.execute("update app.datasets set demo_refs = %s where id = %s",
                           (Jsonb({"users": {"clerk": w.refs["clerk"]}, "vendors": {"acme": w.refs["acme"]}}), w.dataset.id))
    await import_all(w.pool, w.ramp, w.keys, w.dataset.id)
    yield w
    await w.core.aclose()
    await w.mock.aclose()


async def vendor_id(w) -> str:
    async with system_tx(w.pool) as conn:
        row = await (await conn.execute("select id from app.vendors where dataset_id = %s and ramp_vendor_id = %s",
                                        (w.dataset.id, w.refs["acme"]))).fetchone()
    return str(row["id"])


async def change(w, who: str, body: dict | None = None, vendor: str | None = None, status: int = 202) -> dict:
    vid = vendor or await vendor_id(w)
    r = await w.core.post(f"/api/datasets/{w.dataset.id}/vendors/{vid}/bank-change", headers=token(w.users[who]),
                          json=body if body is not None else {"routing_number": ROUTING, "account_number": ACCOUNT})
    assert r.status_code == status, r.text
    assert_matches(SPEC, "post", status, r.json())
    return r.json()


async def mock_accounts(w) -> int:
    """How many accounts the mock holds for the vendor: a refusal must leave it unchanged."""
    return len((await w.mock.get(f"/developer/v1/vendors/{w.refs['acme']}/accounts", headers=w.auth)).json()["data"])


async def test_vendor_accounts_list_routing_and_last_four_and_who_can_change(demo):
    body = await get(demo, f"/api/datasets/{demo.dataset.id}/vendor-accounts", "clerk", "/api/datasets/{dataset_id}/vendor-accounts")
    assert body["can_change"] is True and body["reason"] is None
    [acme] = body["vendors"]
    assert acme["legal_name"] == "Acme Fasteners LLC"
    assert acme["account"]["last4"] == "4444" and acme["account"]["routing_number"] == "121000248"
    assert acme["open_case_id"] is None
    unlinked = await get(demo, f"/api/datasets/{demo.dataset.id}/vendor-accounts", "approver1", "/api/datasets/{dataset_id}/vendor-accounts")
    assert unlinked["can_change"] is False and "linked" in unlinked["reason"]


async def test_a_change_runs_the_real_pipeline_as_the_clerk_who_then_cannot_release_it(demo):
    result = await change(demo, "clerk")
    assert result["account"] == {"routing_number": ROUTING, "last4": ACCOUNT[-4:]}
    await demo.worker.drain(demo.dataset.id)  # the webhook already arrived; the worker places the hold
    case = await case_of(demo)
    assert case is not None and case["entered_by"] == demo.users["clerk"]
    detail = await get(demo, f"/api/cases/{case['id']}", "clerk", "/api/cases/{case_id}")
    assert detail["change"]["new_account"] == {"routing_number": ROUTING, "last4": ACCOUNT[-4:]}
    assert any("R5" in r for r in detail["permissions"]["reasons"])
    listed = await get(demo, f"/api/datasets/{demo.dataset.id}/vendor-accounts", "clerk", "/api/datasets/{dataset_id}/vendor-accounts")
    assert listed["vendors"][0]["open_case_id"] == str(case["id"])


async def test_the_full_account_number_is_never_stored_returned_or_logged(demo, caplog):
    caplog.set_level(logging.DEBUG)
    result = await change(demo, "clerk")
    await demo.worker.drain(demo.dataset.id)
    assert ACCOUNT not in str(result)
    assert ACCOUNT not in caplog.text
    async with system_tx(demo.pool) as conn:
        tables = await (await conn.execute(
            "select table_schema, table_name from information_schema.tables "
            "where table_schema in ('app', 'audit') and table_type = 'BASE TABLE'")).fetchall()
        for t in tables:
            hit = await (await conn.execute(
                f'select count(*) as n from "{t["table_schema"]}"."{t["table_name"]}" x where x::text like %s', (f"%{ACCOUNT}%",))).fetchone()
            assert hit["n"] == 0, f"{t['table_schema']}.{t['table_name']} holds the full account number"


@pytest.mark.parametrize("body", [
    {"routing_number": "026009594", "account_number": ACCOUNT},     # checksum fails
    {"routing_number": "02600959", "account_number": ACCOUNT},      # eight digits
    {"routing_number": ROUTING, "account_number": "123"},           # too short
    {"routing_number": ROUTING, "account_number": "12345678901234567890"},  # too long
    {"routing_number": ROUTING, "account_number": "1234-5678"},     # not digits
])
async def test_a_malformed_number_is_refused_without_echoing_it(demo, body):
    before = await mock_accounts(demo)
    r = await change(demo, "clerk", body=body, status=422)
    assert body["account_number"] not in r["message"] and body["routing_number"] not in r["message"]
    assert await mock_accounts(demo) == before


async def test_a_person_with_no_linked_ramp_user_is_refused(demo):
    before = await mock_accounts(demo)
    r = await change(demo, "approver1", status=403)
    assert "linked" in r["message"]
    assert await mock_accounts(demo) == before


async def test_an_unknown_vendor_or_one_from_another_dataset_is_not_found(demo, pool):
    await change(demo, "clerk", vendor=str(uuid.uuid4()), status=404)
    other = await make_world(pool, kind="demo")
    await import_all(other.pool, other.ramp, other.keys, other.dataset.id)
    await change(demo, "clerk", vendor=await vendor_id(other), status=404)
    await other.core.aclose()
    await other.mock.aclose()


async def test_only_the_current_demo_against_the_mock(pool):
    dev = await make_world(pool, kind="dev")
    await import_all(dev.pool, dev.ramp, dev.keys, dev.dataset.id)
    before = await mock_accounts(dev)
    r = await change(dev, "clerk", status=403)
    assert "demo" in r["message"]
    listed = await get(dev, f"/api/datasets/{dev.dataset.id}/vendor-accounts", "clerk", "/api/datasets/{dataset_id}/vendor-accounts")
    assert listed["can_change"] is False and listed["reason"] == r["message"]
    assert await mock_accounts(dev) == before
    await dev.core.aclose()
    await dev.mock.aclose()


async def test_a_retired_demo_and_the_sandbox_are_refused(demo):
    app = demo.core._transport.app
    app.state.settings = dataclasses.replace(app.state.settings, ramp_mode=RampMode.SANDBOX)
    assert "mock" in (await change(demo, "clerk", status=403))["message"]
    app.state.settings = dataclasses.replace(app.state.settings, ramp_mode=RampMode.MOCK)
    async with system_tx(demo.pool) as conn:
        await conn.execute("update app.datasets set retired_at = now() where id = %s", (demo.dataset.id,))
    assert "current demo" in (await change(demo, "clerk", status=403))["message"]


async def test_signed_out_is_401(demo):
    r = await demo.core.post(f"/api/datasets/{demo.dataset.id}/vendors/{await vendor_id(demo)}/bank-change",
                             json={"routing_number": ROUTING, "account_number": ACCOUNT})
    assert r.status_code == 401
