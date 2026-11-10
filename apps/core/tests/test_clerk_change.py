"""A clerk's bank change from the dashboard (spec 0077): demo datasets and the Ramp mock only, made
as the clerk's linked Ramp user, then the real pipeline. Every refusal sends nothing to Ramp, and the
full account number is never stored, logged or returned (ADR-8)."""
from __future__ import annotations

import dataclasses
import logging
import uuid

import httpx
import pytest
from conftest import make_world
from contract import assert_matches
from dialback_core import api
from dialback_core.db import system_tx
from dialback_core.detection import import_all
from dialback_core.flags import RampMode
from flows import case_of, report, transit
from psycopg.types.json import Jsonb
from test_api import get, token

pytestmark = pytest.mark.anyio

ROUTING = "026009593"            # a valid ABA routing number (Bank of America's, fictional account)
ACCOUNT = "987654321098"         # the full number the clerk types; must never be stored or returned
SPEC = "/api/datasets/{dataset_id}/vendors/{vendor_id}/bank-change"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    """Most tests make one change after another; the one-a-minute limit has its own test."""
    monkeypatch.setattr(api, "CHANGE_EVERY_SECONDS", 0)


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
    unlinked = await get(demo, f"/api/datasets/{demo.dataset.id}/vendor-accounts", "clerk2", "/api/datasets/{dataset_id}/vendor-accounts")
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
    assert detail["change"]["detected_via"] == "webhook"
    listed = await get(demo, f"/api/datasets/{demo.dataset.id}/vendor-accounts", "clerk", "/api/datasets/{dataset_id}/vendor-accounts")
    assert listed["vendors"][0]["open_case_id"] == str(case["id"])
    # The call, with the clerk's new last four given, makes it Verified; then Postgres refuses the clerk
    # and an approver releases.
    r = await report(demo, case["id"], demo.voice.placed[-1]["call_id"], answers=transit(demo.voice.placed[-1]["call_id"], digits=ACCOUNT[-4:]))
    assert r.status_code in (200, 204), r.text
    await demo.worker.drain(demo.dataset.id)
    case = await case_of(demo)
    assert case["state"] == "awaiting_release", case["state"]
    body = {"expected_version": case["version"], "action": "approve", "reason": "Confirmed on the ledger's number."}
    refused = await demo.core.post(f"/api/cases/{case['id']}/release", headers=token(demo.users["clerk"]), json=body)
    assert refused.status_code == 403, refused.text
    released = await demo.core.post(f"/api/cases/{case['id']}/release", headers=token(demo.users["approver1"]), json=body)
    assert released.status_code == 200, released.text


async def test_the_full_account_number_is_never_stored_returned_or_logged(demo, caplog):
    caplog.set_level(logging.DEBUG)
    result = await change(demo, "clerk")
    await demo.worker.drain(demo.dataset.id)
    assert ACCOUNT not in str(result)
    assert ACCOUNT not in caplog.text
    async with system_tx(demo.pool) as conn:
        tables = await (await conn.execute(
            "select table_schema, table_name from information_schema.tables "
            "where table_schema in ('app', 'audit', 'ledger', 'voice', 'lab') and table_type = 'BASE TABLE'")).fetchall()
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


async def test_extra_fields_and_long_values_are_refused_without_echo(demo):
    for body in ({"routing_number": ROUTING, "account_number": ACCOUNT, "note": ACCOUNT},
                 {"routing_number": ROUTING, "account_number": ACCOUNT * 6}):
        r = await demo.core.post(f"/api/datasets/{demo.dataset.id}/vendors/{await vendor_id(demo)}/bank-change",
                                 headers=token(demo.users["clerk"]), json=body)
        assert r.status_code == 422 and ACCOUNT not in r.text


async def test_the_current_account_retyped_is_refused(demo):
    before = await mock_accounts(demo)
    r = await change(demo, "clerk", body={"routing_number": "121000248", "account_number": "000033334444"}, status=422)
    assert "already" in r["message"]
    assert await mock_accounts(demo) == before


async def test_one_change_a_minute_per_dataset(demo, monkeypatch):
    monkeypatch.setattr(api, "CHANGE_EVERY_SECONDS", 60)
    await change(demo, "clerk")
    before = await mock_accounts(demo)
    r = await change(demo, "clerk", body={"routing_number": ROUTING, "account_number": "111122223333"}, status=409)
    assert "minute" in r["message"]
    assert await mock_accounts(demo) == before


async def test_a_second_change_on_an_open_case_is_its_next_version(demo):
    await change(demo, "clerk")
    await demo.worker.drain(demo.dataset.id)
    first = await case_of(demo)
    await change(demo, "clerk", body={"routing_number": ROUTING, "account_number": "111122223333"})
    await demo.worker.drain(demo.dataset.id)
    again = await case_of(demo)
    assert again["id"] == first["id"] and again["version"] == first["version"] + 1


async def test_only_a_clerk_can_send_a_change(demo):
    async with system_tx(demo.pool) as conn:  # move the Ramp link from the clerk to an approver
        await conn.execute("update app.user_profiles set ramp_user_id = null where user_id = %s", (demo.users["clerk"],))
        await conn.execute("update app.user_profiles set ramp_user_id = %s where user_id = %s", (demo.refs["clerk"], demo.users["approver1"]))
    r = await change(demo, "approver1", status=403)
    assert "clerk" in r["message"]


async def test_a_link_to_a_ramp_user_outside_this_demo_is_refused(demo):
    async with system_tx(demo.pool) as conn:
        await conn.execute("update app.user_profiles set ramp_user_id = %s where user_id = %s", ("usr_elsewhere", demo.users["clerk"]))
    assert "linked" in (await change(demo, "clerk", status=403))["message"]


@pytest.mark.parametrize("fault,sent", [(httpx.ConnectError("down"), False), (httpx.ReadTimeout("lost"), True)])
async def test_a_mock_that_is_down_or_silent_is_502_and_says_which(demo, caplog, fault, sent):
    caplog.set_level(logging.DEBUG)

    def raise_it(request: httpx.Request) -> httpx.Response:
        raise fault
    connector = demo.core._transport.app.state.connector
    real, connector._http = connector._http, httpx.AsyncClient(transport=httpx.MockTransport(raise_it))
    try:
        r = await change(demo, "clerk", status=502)
    finally:
        await connector._http.aclose()
        connector._http = real
    assert r["code"] == "upstream_unavailable"
    assert ("may have been made" in r["message"]) is sent and ("Nothing was changed" in r["message"]) is not sent
    assert ACCOUNT not in r["message"] and ACCOUNT not in caplog.text


async def test_a_person_with_no_linked_ramp_user_is_refused(demo):
    before = await mock_accounts(demo)
    r = await change(demo, "clerk2", status=403)
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
    before = await mock_accounts(demo)
    refused = await change(demo, "clerk", status=403)
    assert "mock" in refused["message"]
    listed = await get(demo, f"/api/datasets/{demo.dataset.id}/vendor-accounts", "clerk", "/api/datasets/{dataset_id}/vendor-accounts")
    assert listed["can_change"] is False and listed["reason"] == refused["message"]
    assert await mock_accounts(demo) == before
    app.state.settings = dataclasses.replace(app.state.settings, ramp_mode=RampMode.MOCK)
    async with system_tx(demo.pool) as conn:
        await conn.execute("update app.datasets set retired_at = now() where id = %s", (demo.dataset.id,))
    assert "current demo" in (await change(demo, "clerk", status=403))["message"]
    assert await mock_accounts(demo) == before


async def test_signed_out_is_401(demo):
    r = await demo.core.post(f"/api/datasets/{demo.dataset.id}/vendors/{await vendor_id(demo)}/bank-change",
                             json={"routing_number": ROUTING, "account_number": ACCOUNT})
    assert r.status_code == 401
