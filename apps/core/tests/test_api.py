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
from flows import add_vendor, case_of, change_and_detect, report
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


async def test_a_first_account_change_shows_no_previous_account(world):
    """Spec 0050: a vendor paid by check gets its first bank account. The detail has no old
    account, matches the contract, and the timeline reads as for any change."""
    await add_vendor(world, "birch", "Birch Supply Co")
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.bank_change(vendor_ref="birch")
    await world.deliver_webhooks()
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert case is not None, "a known vendor's first account opens a case"

    detail = await get(world, f"/api/cases/{case['id']}", "approver1", "/api/cases/{case_id}")
    assert detail["change"]["old_account"] is None and detail["change"]["new_account"]["last4"] == "6666"
    assert detail["timeline"][0]["summary"] == "A bank-detail change was detected for this vendor (via webhook)."
    assert [e["type"] for e in detail["timeline"]] == ["change_detected", "hold_confirmed", "number_resolved", "call_started"]
    assert detail["chain"]["ok"] and detail["entered_by"]["display_name"] == "Clerk"


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


async def test_only_an_approver_reviews(world):
    """Spec 0028: anyone can sign up (spec 0027), so clearing or blocking a review needs approver."""
    world.voice.refuse = "outside_calling_window"
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    cid = str((await case_of(world))["id"])
    view = await get(world, f"/api/cases/{cid}", "clerk2", "/api/cases/{case_id}")
    assert view["case"]["state"] == "needs_review"
    assert view["permissions"]["can_review"] is False
    assert any("approver role" in r for r in view["permissions"]["reasons"])
    for action in ("clear", "block"):
        body = {"expected_version": 1, "action": action, "reason": "I called them and they confirmed."}
        refused = await post(world, f"/api/cases/{cid}/review", "clerk2", "/api/cases/{case_id}/review", body, status=403)
        assert refused["code"] == "forbidden" and "approver role" in refused["message"]
    assert (await case_of(world))["state"] == "needs_review", "nothing moved"
    assert (await get(world, f"/api/cases/{cid}", "approver1", "/api/cases/{case_id}"))["permissions"]["can_review"] is True
    clear = {"expected_version": 1, "action": "clear", "reason": "Reached the CFO on the 2023 contract number; confirmed."}
    assert (await post(world, f"/api/cases/{cid}/review", "approver1", "/api/cases/{case_id}/review", clear))["to_state"] == "awaiting_release"
    # Past review, an approver is told the state, never that they lack a role they have.
    again = await post(world, f"/api/cases/{cid}/review", "approver2", "/api/cases/{case_id}/review", clear, status=409)
    assert again["code"] == "transition_refused" and "approver role" not in again["message"]


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


async def test_a_refused_calls_alert_names_the_reason(world):
    """Spec 0031: a pre-dial refusal is the system working, so the banner says why, in the timeline's words."""
    from dialback_core.timeline import alert_message
    world.voice.refuse = "not_on_dial_allowlist"
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    ds = str(world.dataset.id)
    alerts = (await get(world, f"/api/datasets/{ds}/alerts", "approver1", "/api/datasets/{dataset_id}/alerts"))["alerts"]
    stuck = [a for a in alerts if a["kind"] == "command_stuck"]
    assert len(stuck) == 1
    assert stuck[0]["message"] == ("The call wasn't placed: the number is not on the team's dial allowlist. "
                                   "The hold stays on and a person must review.")
    cid = str((await case_of(world))["id"])
    detail = await get(world, f"/api/cases/{cid}", "approver1", "/api/cases/{case_id}")
    refusal = next(e for e in detail["timeline"] if e["type"] == "command_failed")
    assert "the number is not on the team's dial allowlist" in refusal["summary"], "the banner and the timeline agree"
    # Anything that isn't a pre-dial refusal keeps its stored text.
    assert alert_message("Command place_call failed after 5 attempts",
                         {"command": "place_call", "last_error": "voice service unreachable: ConnectError"}) \
        == "Command place_call failed after 5 attempts"
    assert alert_message("Command release_hold failed after 5 attempts", None) == "Command release_hold failed after 5 attempts"


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


# The vendor risk list (spec 0026) ------------------------------------------------------------------

RISK = "/api/datasets/{dataset_id}/vendor-risk"


async def test_the_vendor_risk_list_reads_the_ledger(world):
    from dialback_ledger.ingest import ingest_all
    from dialback_ledger.store import add_vendor_domain, write_ingest
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    truth = json.loads((Path(__file__).resolve().parents[3] / "corpus/truth/C01.json").read_text())
    docs = json.loads((Path(__file__).resolve().parents[3] / "corpus" / truth["documents"]).read_text())
    async with world.pool.connection() as conn, conn.transaction():
        vendor = (await (await conn.execute("select id from app.vendors where dataset_id = %s order by legal_name limit 1",
                                            (world.dataset.id,))).fetchone())["id"]
        await write_ingest(conn, dataset_id=world.dataset.id, vendor_id=vendor, result=ingest_all(docs, extractor_version="t"))
        await add_vendor_domain(conn, dataset_id=world.dataset.id, vendor_id=vendor, domain=truth["vendor"]["domain"])
    body = await get(world, f"/api/datasets/{world.dataset.id}/vendor-risk", "clerk", RISK)
    (row,) = body["vendors"]                      # only vendors the ledger has documents for
    assert row["vendor_id"] == str(vendor) and row["status"] == "missing" and row["number_on_file"] is None
    assert row["provable_number"] == truth["expected"]["pick"] and row["status_text"]


async def test_the_vendor_risk_list_of_an_unknown_dataset_is_not_found(world):
    await get(world, f"/api/datasets/{uuid.uuid4()}/vendor-risk", "clerk", RISK, status=404)


async def test_the_case_shows_its_calls_answers_and_checks(world):
    """Spec 0035: the call comes from the evidence chain; account_last4 is a digit count, never the digits."""
    case = await verified_case(world)
    detail = await get(world, f"/api/cases/{case['id']}", "approver1", "/api/cases/{case_id}")
    [call] = detail["calls"]
    assert (call["attempt"], call["result"], call["answered_by"], call["direction"], call["dialed_number_is_pick"]) \
        == (1, "answers_recorded", "human", "outbound", True)
    assert call["started_at"] and call["ended_at"] and call["last_four_result"] == "match"
    assert call["checks"]["disclosure_first"] is True and call["checks"]["speech_guard_trips"] == 0
    slots = {a["slot"]: a for a in call["answers"]}
    assert slots["change_requested"]["value"] == "yes" and slots["change_requested"]["turn_id"] == 7
    assert "V07_right_person_confirmed" in slots["change_requested"]["feeds"]
    last4 = slots["account_last4"]
    assert (last4["value"], last4["digit_count"], last4["capture_method"]) == (None, 4, "keypad")
    assert "V08_digits_match" in last4["feeds"]
    raw = json.dumps(detail)
    assert "6666\"" not in raw.replace('"last4": "6666"', "") and "digits_hmac" not in raw, "no trace of the spoken digits"
    script = detail["call_script"]
    assert script["disclosure"].startswith("Hi, this is an AI assistant on a recorded line")
    assert [q["slot"] for q in script["questions"]][:2] == ["recording_consent", "company_named"]
    assert "transcript" in script["note"]

    d = detail["decision"]
    assert [c["status"] for c in d["checklist"]] == ["passed"] * 11, "11 conditions since V05 was retired (spec 0082, D7b)"
    assert d["checklist"][6] == {"id": "V08_digits_match", "sentence": "The digits the person gave match the new account.",
                                 "status": "passed"}
    assert {f["block"] for f in d["fired"]} == {"verified"} and "approver" in d["effect"]


async def test_a_refused_call_has_no_attempt(world):
    world.voice.refuse = "not_on_dial_allowlist"
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    detail = await get(world, f"/api/cases/{(await case_of(world))['id']}", "approver1", "/api/cases/{case_id}")
    assert detail["calls"] == [] and detail["decision"] is None


async def test_a_denial_is_not_verified_and_its_checklist_was_not_run(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    from flows import transit
    call_id = world.voice.placed[-1]["call_id"]
    await report(world, case["id"], call_id, answers=transit(call_id, change="no"))
    await world.worker.drain(world.dataset.id)
    d = (await get(world, f"/api/cases/{case['id']}", "approver1", "/api/cases/{case_id}"))["decision"]
    assert d["outcome"] == "not_verified"
    assert d["fired"] == [{"id": "NV01_vendor_denied_change", "block": "not_verified",
                           "sentence": "The person said the vendor did not ask to change its bank details."}]
    assert {c["status"] for c in d["checklist"]} == {"not_checked"} and "Not verified" in d["checklist_note"]
    assert "blocked" in d["effect"]


async def test_the_evidence_log_points_at_a_hand_edited_row(world, database_url):
    world.voice.refuse = "not_on_dial_allowlist"
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    ds = str(world.dataset.id)
    spec = "/api/datasets/{dataset_id}/evidence"
    log = await get(world, f"/api/datasets/{ds}/evidence", "approver1", spec)
    assert log["chain"]["ok"] is True and log["total"] == len(log["events"]) >= 3
    assert [e["seq"] for e in log["events"]] == sorted((e["seq"] for e in log["events"]), reverse=True)
    assert all(e["content_ok"] and e["link_ok"] for e in log["events"])
    first = log["events"][-1]
    assert (first["seq"], first["prev_hash"], first["vendor"]) == (1, "0" * 64, "Acme Fasteners LLC")

    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as owner:
        await owner.execute("alter table audit.evidence_events disable trigger evidence_events_no_update")
        try:
            await owner.execute("update audit.evidence_events set body_jcs = replace(body_jcs, 'webhook', 'manual') "
                                "where chain_id = %s and seq = 1", (world.dataset.chain_id,))
        finally:
            await owner.execute("alter table audit.evidence_events enable trigger evidence_events_no_update")
    assert log["last_check"] is None, "the verify job hasn't run on this dataset"
    await world.worker.jobs.run(world.dataset.id, "verify_chain", force=True)
    log = await get(world, f"/api/datasets/{ds}/evidence", "approver1", spec, limit=2)
    assert (log["chain"]["ok"], log["chain"]["first_bad_seq"], len(log["events"])) == (False, 1, 2)
    check = log["last_check"]
    assert (check["ok"], check["first_bad_seq"]) == (False, 1), "what the job found, not whether the job ran"
    assert check["finished_at"] and "body edited" in check["detail"]
    full = await get(world, f"/api/datasets/{ds}/evidence", "approver1", spec)
    bad = [e["seq"] for e in full["events"] if not e["content_ok"]]
    assert bad == [1] and all(e["link_ok"] for e in full["events"]), "only the edited row's body fails; the links still hold"
    await get(world, f"/api/datasets/{uuid.uuid4()}/evidence", "approver1", spec, status=404)


async def test_a_demo_account_is_refused_outside_demo_data(world, database_url):
    """Spec 0034: a demo account can't see a dev dataset's cases, so it can't act on them either. (The database's
    own refusal, for a caller that gets past this, is supabase/tests/015_demo_accounts.sql.)"""
    case = await verified_case(world)
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as owner:  # the team, as the owner
        await owner.execute("insert into app.demo_accounts (user_id) values (%s)", (world.users["approver1"],))
    cid = str(case["id"])
    await get(world, f"/api/cases/{cid}", "approver1", "/api/cases/{case_id}", status=404)  # it can't even see the case
    for action in ("approve", "reject"):
        await post(world, f"/api/cases/{cid}/release", "approver1", "/api/cases/{case_id}/release",
                   {"expected_version": 1, "action": action, "reason": "Demo."}, status=404)
    assert (await case_of(world))["state"] == "awaiting_release"
    seen = {d["id"] for d in (await get(world, "/api/datasets", "approver1", "/api/datasets"))["datasets"]}
    assert str(world.dataset.id) not in seen, "a dev dataset is hidden from a demo account"
    assert (await get(world, f"/api/cases/{cid}", "approver2", "/api/cases/{case_id}"))["case"]["state"] == "awaiting_release"
