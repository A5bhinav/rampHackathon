"""The dashboard API: every response matches dialback.yaml, every request runs in Postgres as
the signed-in person, and the database (not the API) decides who may act."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import jwt
import psycopg
import pytest
from conftest import JWT_SECRET
from contract import assert_matches
from dialback_core.db import system_tx
from dialback_core.detection import import_all
from dialback_core.flags import ResolverMode
from dialback_core.ports import LedgerResolver
from flows import case_of, change_and_detect, report
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.anyio


def token(user_id: uuid.UUID, secret: bytes = JWT_SECRET, ttl: int = 600) -> dict[str, str]:
    claims = {"sub": str(user_id), "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + ttl}
    return {"Authorization": f"Bearer {jwt.encode(claims, secret, algorithm='HS256')}"}


async def get(world, url: str, who: str, spec: str, status: int = 200, **params):
    r = await world.core.get(url, headers=token(world.users[who]), params=params)
    assert r.status_code == status, r.text
    assert_matches(spec, "get", status, r.json())
    return r.json()


async def post(world, url: str, who: str, spec: str, body: dict, status: int = 200):
    r = await world.core.post(url, headers=token(world.users[who]), json=body)
    assert r.status_code == status, r.text
    assert_matches(spec, "post", status, r.json())
    return r.json()


async def verified_case(world, actor="clerk"):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world, actor=actor)
    case = await case_of(world)
    await report(world, case["id"], world.voice.placed[-1]["call_id"])
    await world.worker.drain(world.dataset.id)
    return await case_of(world)


async def test_every_request_needs_a_valid_token(world):
    r = await world.core.get("/api/me")
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"
    assert_matches("/api/me", "get", 401, r.json())
    assert (await world.core.get("/api/me", headers=token(world.users["approver1"], secret=b"x" * 40))).status_code == 401
    assert (await world.core.get("/api/me", headers=token(world.users["approver1"], ttl=-10))).status_code == 401


async def test_me_and_datasets(world):
    me = await get(world, "/api/me", "approver1", "/api/me")
    assert me["roles"] == ["approver"] and me["display_name"] == "Approver1"
    datasets = await get(world, "/api/datasets", "approver1", "/api/datasets")
    assert str(world.dataset.id) in [d["id"] for d in datasets["datasets"]]


async def test_queue_detail_and_release(world):
    case = await verified_case(world)
    cid, ds = str(case["id"]), str(world.dataset.id)

    queue = await get(world, "/api/cases", "approver1", "/api/cases", dataset_id=ds)
    [row] = queue["cases"]
    assert (row["state"], row["hold_status"], row["bills_held"], row["next_payment_date"]) == ("awaiting_release", "confirmed", 2, "2026-11-05")

    detail = await get(world, f"/api/cases/{cid}", "approver1", "/api/cases/{case_id}")
    assert [e["type"] for e in detail["timeline"]] == ["change_detected", "hold_confirmed", "number_resolved", "call_started",
                                                       "call_ended", "decision_made"]
    assert detail["timeline"][4]["summary"] == "Call attempt 1 ended with answers recorded; the digits given matched the new account."
    assert detail["decision"]["outcome"] == "verified" and "We dialed the number the Trust Ledger chose for this change." in detail["decision"]["explanation"]
    assert detail["chain"]["ok"] and detail["entered_by"]["display_name"] == "Clerk"
    assert (detail["change"]["old_account"]["last4"], detail["change"]["new_account"]["last4"]) == ("4444", "6666")
    assert detail["permissions"]["can_approve"] is True

    clerk_view = await get(world, f"/api/cases/{cid}", "clerk", "/api/cases/{case_id}")
    assert clerk_view["permissions"]["can_approve"] is False and any("(R5)" in r for r in clerk_view["permissions"]["reasons"])

    # The database refuses the changer even when they hold the approver role (R5).
    async with system_tx(world.pool) as conn:
        await conn.execute("insert into app.user_roles (user_id, role) values (%s, 'approver')", (world.users["clerk"],))
    refused = await post(world, f"/api/cases/{cid}/release", "clerk", "/api/cases/{case_id}/release",
                         {"expected_version": 1, "action": "approve", "reason": "Looks fine."}, status=403)
    assert "(R5)" in refused["message"]
    stale = await post(world, f"/api/cases/{cid}/release", "approver1", "/api/cases/{case_id}/release",
                       {"expected_version": 2, "action": "approve", "reason": "Confirmed."}, status=409)
    assert stale["code"] == "version_conflict"

    body = {"expected_version": 1, "action": "approve", "reason": "Confirmed on the ledger's number; digits match."}
    done = await post(world, f"/api/cases/{cid}/release", "approver1", "/api/cases/{case_id}/release", body)
    assert (done["status"], done["to_state"]) == ("applied", "releasing")
    again = await post(world, f"/api/cases/{cid}/release", "approver1", "/api/cases/{case_id}/release", body)
    assert again["status"] == "already_applied"

    await world.worker.drain(world.dataset.id)
    detail = await get(world, f"/api/cases/{cid}", "approver1", "/api/cases/{case_id}")
    assert detail["case"]["state"] == "released" and detail["hold"]["status"] == "released"
    assert detail["permissions"]["reasons"] == ["This case is released; the hold is off and nothing is left to decide."]
    assert detail["timeline"][-1]["summary"] == "The hold was lifted in Ramp and 2 payment dates restored."
    assert detail["releases"][0]["actor"]["display_name"] == "Approver1"


async def test_an_unknown_changer_needs_two_approvers(world):
    case = await verified_case(world, actor=None)
    cid = str(case["id"])
    body = {"expected_version": 1, "action": "approve", "reason": "Evidence reviewed."}
    first = await post(world, f"/api/cases/{cid}/release", "approver1", "/api/cases/{case_id}/release", body)
    assert first["status"] == "awaiting_second_approver" and first["to_state"] == "awaiting_release"
    view = await get(world, f"/api/cases/{cid}", "approver1", "/api/cases/{case_id}")
    assert view["permissions"]["can_approve"] is False and view["case"]["unknown_changer"] is True
    second = await post(world, f"/api/cases/{cid}/release", "approver2", "/api/cases/{case_id}/release", body)
    assert second["to_state"] == "releasing"


async def test_review_then_a_different_approver_releases(world):
    world.voice.refuse = "outside_calling_window"
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    cid = str((await case_of(world))["id"])
    clear = {"expected_version": 1, "action": "clear", "reason": "Reached the CFO on the 2023 contract number; confirmed."}
    assert "(R5)" in (await post(world, f"/api/cases/{cid}/review", "clerk", "/api/cases/{case_id}/review", clear, status=403))["message"]
    cleared = await post(world, f"/api/cases/{cid}/review", "approver1", "/api/cases/{case_id}/review", clear)
    assert cleared["to_state"] == "awaiting_release"

    release = {"expected_version": 1, "action": "approve", "reason": "Reviewer's call is enough."}
    refused = await post(world, f"/api/cases/{cid}/release", "approver1", "/api/cases/{case_id}/release", release, status=403)
    assert refused["code"] == "forbidden"
    assert (await post(world, f"/api/cases/{cid}/release", "approver2", "/api/cases/{case_id}/release", release))["to_state"] == "releasing"
    detail = await get(world, f"/api/cases/{cid}", "approver2", "/api/cases/{case_id}")
    refusal = next(e for e in detail["timeline"] if e["type"] == "command_failed")
    assert refusal["summary"].startswith("The voice service checked the call and did not place it (outside the vendor's calling hours)")


async def test_alerts_and_a_hand_edited_evidence_row(world, database_url):
    world.voice.refuse = "not_on_dial_allowlist"
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    ds = str(world.dataset.id)
    alerts = await get(world, f"/api/datasets/{ds}/alerts", "approver1", "/api/datasets/{dataset_id}/alerts")
    assert alerts["alerts"][0]["severity"] == "critical"
    assert (await get(world, f"/api/datasets/{ds}/chain", "approver1", "/api/datasets/{dataset_id}/chain"))["ok"] is True

    # The demo's beat: someone with owner rights edits one evidence row by hand.
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as owner:
        await owner.execute("alter table audit.evidence_events disable trigger evidence_events_no_update")
        try:
            await owner.execute("update audit.evidence_events set body_jcs = replace(body_jcs, 'webhook', 'manual') "
                                "where chain_id = %s and seq = 1", (world.dataset.chain_id,))
        finally:
            await owner.execute("alter table audit.evidence_events enable trigger evidence_events_no_update")
    chain = await get(world, f"/api/datasets/{ds}/chain", "approver1", "/api/datasets/{dataset_id}/chain")
    assert (chain["ok"], chain["first_bad_seq"]) == (False, 1)


async def test_not_found_and_invalid_requests(world):
    missing = str(uuid.uuid4())
    await get(world, f"/api/cases/{missing}", "approver1", "/api/cases/{case_id}", status=404)
    await get(world, f"/api/datasets/{missing}/chain", "approver1", "/api/datasets/{dataset_id}/chain", status=404)
    bad = await post(world, f"/api/cases/{missing}/release", "approver1", "/api/cases/{case_id}/release",
                     {"expected_version": 1, "action": "approve", "reason": "   "}, status=422)
    assert bad["code"] == "invalid_request"


def test_the_timeline_says_plainly_when_no_digits_were_given():
    from dialback_core.timeline import summary
    body = {"attempt": 1, "result": "answers_recorded", "last_four_result": "not_provided"}
    assert summary("call_ended", body, "Dialback") == "Call attempt 1 ended with answers recorded; the person did not give four digits."


# The number lineage panel (spec 0023) ---------------------------------------------------------

SELECTION_EXAMPLE = json.loads((Path(__file__).resolve().parents[3]
                                / "packages/contracts/examples/selection.picked.json").read_text())


async def _store_selection(world, case: dict) -> dict:
    """Store the selection the case's number_resolved event names, as the ledger wiring will."""
    async with world.pool.connection() as conn:
        resolved = await (await conn.execute(
            "select body_jcs::jsonb -> 'body' as b from audit.evidence_events where case_id = %s "
            "and type = 'number_resolved' order by seq desc limit 1", (case["id"],))).fetchone()
        sel_id, point = resolved["b"]["number_selection_id"], resolved["b"]["contact_point_id"]
        doc = SELECTION_EXAMPLE | {"number_selection_id": sel_id, "case_id": str(case["id"]),
                                   "dataset_id": str(world.dataset.id), "vendor_id": str(case["vendor_id"]),
                                   "contact_point_id": point}
        await conn.execute("insert into ledger.contact_points (id, dataset_id, vendor_id, e164, first_seen_at, status) "
                           "values (%s, %s, %s, %s, now() - interval '3 years', 'eligible')",
                           (point, world.dataset.id, case["vendor_id"], doc["e164"]))
        await conn.execute("insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, "
                           "rule_version, inputs_sha256, selection) values (%s, %s, %s, %s, %s, 'picked', %s, %s, %s)",
                           (sel_id, world.dataset.id, case["id"], case["vendor_id"], point, doc["rule_version"],
                            doc["inputs_sha256"], Jsonb(doc)))
    return doc


async def test_the_selection_the_case_used_is_served(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    stored = await _store_selection(world, case)
    spec = "/api/cases/{case_id}/selection"
    body = await get(world, f"/api/cases/{case['id']}/selection", "approver1", spec)
    assert body["selection"] == stored
    assert all(text.startswith("Not provable:") for text in body["reason_text"].values())


async def test_no_stored_selection_is_null_not_an_error(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)  # the stand-in resolver's selection is in no table
    body = await get(world, f"/api/cases/{case['id']}/selection", "clerk", "/api/cases/{case_id}/selection")
    assert body["selection"] is None and body["reason_text"]


async def test_an_unavailable_ledger_is_null_with_its_note(world):
    world.worker.resolver = LedgerResolver(ResolverMode.LEDGER)  # no select_number yet: no_provable_number, no id
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    body = await get(world, f"/api/cases/{case['id']}/selection", "clerk", "/api/cases/{case_id}/selection")
    assert body["selection"] is None and body["notes"]["no_selection"]


async def test_the_selection_of_an_unknown_case_is_not_found(world):
    await get(world, f"/api/cases/{uuid.uuid4()}/selection", "clerk", "/api/cases/{case_id}/selection", status=404)
