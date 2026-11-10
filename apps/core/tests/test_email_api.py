"""The dashboard's view of the email door (spec 0089): `GET /api/datasets/{id}/mail` and the
`change.email` of a case opened by an email. Never an email body, never a full account number."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from conftest import make_world
from dialback_core.db import system_tx
from dialback_core.detection import import_all
from flows import case_of, change_and_detect, one
from test_api import get, token

pytestmark = pytest.mark.anyio

MAIL = "/api/datasets/{dataset_id}/mail"
CASE = "/api/cases/{case_id}"
BODY_MARKER = "BODY-MARKER-7c41"
ACCOUNT = "000123454821"          # a full number that must never come back (ADR-8)
T0 = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)


async def put(pool, dataset_id, n, **cols) -> int:
    """Insert one inbound_emails row the way the door leaves it (masked sender and subject, no body)."""
    row = {"dataset_id": dataset_id, "resend_email_id": f"em_{n}_{uuid.uuid4().hex[:6]}", "sender": f"s{n}@vendor.example",
           "subject": f"subject {n}", "received_at": T0 + dt.timedelta(minutes=n), "has_attachments": False} | cols
    names = ", ".join(row)
    async with system_tx(pool) as conn:
        r = await (await conn.execute(f"insert into app.inbound_emails ({names}) values ({', '.join(['%s'] * len(row))}) returning id",
                                      tuple(row.values()))).fetchone()
    return r["id"]


@pytest.fixture
async def world(world):
    """The seeded business, imported (vendors and accounts on file)."""
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    return world


async def acme(w) -> dict:
    return await one(w.pool, "select id, legal_name from app.vendors where dataset_id = %s limit 1", w.dataset.id)


async def test_mail_lists_outcomes_newest_first_and_matches_the_contract(world):
    v = await acme(world)
    await put(world.pool, world.dataset.id, 1, outcome="not_flagged")
    second = await put(world.pool, world.dataset.id, 2, outcome="case_opened", flagged=True, reply="sent", vendor_id=v["id"],
                       routing_number="026009593", last4="4821", red_flags=["lookalike_domain", "sender_off_domain"])
    await put(world.pool, world.dataset.id, 3, outcome="pending", subject=None)
    body = await get(world, MAIL.format(dataset_id=world.dataset.id), "clerk", MAIL)
    items = body["items"]
    assert [i["subject"] for i in items] == [None, "subject 2", "subject 1"]
    mid = items[1]
    assert mid["id"] == second and mid["flagged"] is True and mid["reply"] == "sent"
    assert mid["red_flags"] == ["lookalike_domain", "sender_off_domain"]
    assert mid["vendor"] == {"id": str(v["id"]), "legal_name": v["legal_name"]}
    assert (mid["routing_number"], mid["last4"], mid["case_id"]) == ("026009593", "4821", None)
    assert items[0]["vendor"] is None and items[0]["red_flags"] == []
    assert mid["received_at"] == "2026-11-10T12:02:00Z"


async def test_mail_is_refused_signed_out_and_scoped_to_the_dataset(world, pool):
    other = await make_world(pool)
    await import_all(other.pool, other.ramp, other.keys, other.dataset.id)
    await put(world.pool, world.dataset.id, 1)
    await put(pool, other.dataset.id, 2, subject="theirs")
    url = MAIL.format(dataset_id=world.dataset.id)
    assert (await world.core.get(url)).status_code == 401
    mine = await get(world, url, "clerk", MAIL)
    assert [i["subject"] for i in mine["items"]] == ["subject 1"]
    r = await world.core.get(MAIL.format(dataset_id=uuid.uuid4()), headers=token(world.users["clerk"]))
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    await other.core.aclose()
    await other.mock.aclose()


async def test_mail_returns_at_most_200_items(world):
    async with system_tx(world.pool) as conn:
        await conn.execute(
            "insert into app.inbound_emails (dataset_id, resend_email_id, sender, received_at, has_attachments) "
            "select %s, 'bulk' || g, 'a@b.example', now() - make_interval(secs => g), false from generate_series(1, 205) g",
            (world.dataset.id,))
    body = await get(world, MAIL.format(dataset_id=world.dataset.id), "clerk", MAIL)
    assert len(body["items"]) == 200


async def test_case_detail_carries_the_email_info(world):
    await change_and_detect(world)
    case = await case_of(world)
    detail = await get(world, CASE.format(case_id=case["id"]), "clerk", CASE)
    assert detail["change"].get("email") is None  # a change from Ramp has no email
    await put(world.pool, world.dataset.id, 1, outcome="case_opened", flagged=True, case_id=case["id"],
              change_event_id=case["change_event_id"], red_flags=["unknown_phone"], subject="New bank details")
    detail = await get(world, CASE.format(case_id=case["id"]), "clerk", CASE)
    assert detail["change"]["email"] == {"sender": "s1@vendor.example", "subject": "New bank details",
                                         "received_at": "2026-11-10T12:01:00Z", "red_flags": ["unknown_phone"]}


async def test_no_mail_response_holds_a_body_or_full_number(world):
    v = await acme(world)
    # The table holds neither; put both through the door's columns anyway and read every response.
    await put(world.pool, world.dataset.id, 1, outcome="case_opened", flagged=True, vendor_id=v["id"], last4=ACCOUNT[-4:],
              routing_number="026009593", subject="new details")
    await change_and_detect(world)
    case = await case_of(world)
    texts = [(await world.core.get(MAIL.format(dataset_id=world.dataset.id), headers=token(world.users["clerk"]))).text,
             (await world.core.get(CASE.format(case_id=case["id"]), headers=token(world.users["clerk"]))).text]
    for text in texts:
        assert BODY_MARKER not in text and ACCOUNT not in text
    items = (await world.core.get(MAIL.format(dataset_id=world.dataset.id), headers=token(world.users["clerk"]))).json()["items"]
    assert all("body" not in i and "text" not in i for i in items)
