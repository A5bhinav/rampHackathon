"""The walking skeleton's money path, end to end in process: a bank change in the Ramp mock
becomes a case, the hold goes on, a call is placed and reported, code decides, a different
person releases, and the bills get their dates back. Plus the ways it fails closed."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from conftest import core_settings, make_world
from dialback_core.db import system_tx
from dialback_core.detection import import_all, sync_vendor
from dialback_core.flags import ResolverMode
from dialback_core.ports import ChangeFacts, LedgerChangeContext, LedgerResolver
from dialback_core.ramp import RampError
from flows import _types, answer, bills, case_of, change_and_detect, one, pay, release, report, transit

pytestmark = pytest.mark.anyio

async def test_a_bank_change_walks_all_the_way_to_release(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)

    case = await case_of(world)
    assert case["state"] == "calling" and case["call_attempts"] == 1
    held = await bills(world)
    assert all(b.is_on_hold and b.payment_date is None for b in held.values()), "the hold went on and cleared the dates"
    assert (await world.ramp.get_vendor(world.refs["acme"])).hold_id is not None
    assert await pay(world, "2026-12-31") == [], "R1: nothing is paid while the case is open"
    [placed] = world.voice.placed
    assert placed["key"] == f"{case['id']}:call:v1:1"

    assert (await report(world, case["id"], placed["call_id"])).status_code == 204
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert (case["state"], case["outcome"]) == ("awaiting_release", "verified")
    decision = await one(world.pool, "select * from app.decisions where case_id = %s", case["id"])
    assert decision["last_four_result"] == "match"
    assert "U01_speaker_thread_not_checked" not in decision["rules_fired"], "the change context reached the engine (spec 0005)"
    assert "U03_offered_number_source_not_checked" in decision["rules_fired"], "still unknown: the double doesn't say"

    ended = await one(world.pool, "select body_jcs from audit.evidence_events where case_id = %s and type = 'call_ended'", case["id"])
    assert '"value":"6666"' not in ended["body_jcs"] and '"digit_count":4' in ended["body_jcs"], "digits are never stored"

    result = await release(world, case["id"], case["version"])
    assert result["to_state"] == "releasing"
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert case["state"] == "released"
    assert (await world.ramp.get_vendor(world.refs["acme"])).hold_id is None
    restored = await bills(world)
    assert {b.payment_date for b in restored.values()} == {dt.date(2026, 11, 5), dt.date(2026, 11, 12)}, "dates restored"
    assert await pay(world, "2026-11-05") == [world.refs["bill-1"]]

    chain = await one(world.pool, "select * from audit.verify_chain(%s)", world.dataset.chain_id)
    assert chain["ok"], chain["detail"]
    types = [r["type"] for r in await _types(world, case["id"])]
    assert types == ["change_detected", "hold_confirmed", "number_resolved", "call_started", "call_ended",
                     "decision_made", "release_approved", "hold_release_confirmed"]


async def test_a_hold_that_fails_once_is_retried(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    ramp, real = world.ramp, world.ramp.place_vendor_hold
    failures = [RampError("Ramp is down", status=503, retryable=True)]

    async def flaky(vendor_id, reason):
        if failures:
            raise failures.pop()
        return await real(vendor_id, reason)

    ramp.place_vendor_hold = flaky
    try:
        await change_and_detect(world)
    finally:
        del ramp.place_vendor_hold
    case = await case_of(world)
    assert case["state"] == "calling"
    hold_cmd = await one(world.pool, "select attempts, status from app.outbox where case_id = %s and command = 'place_hold'", case["id"])
    assert (hold_cmd["attempts"], hold_cmd["status"]) == (2, "done")


async def test_a_hold_that_keeps_failing_goes_to_a_person_with_the_case_held_open(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    ramp = world.ramp

    async def down(vendor_id, reason):
        raise RampError("Ramp is down", status=503, retryable=True)

    ramp.place_vendor_hold = down
    try:
        await change_and_detect(world)
    finally:
        del ramp.place_vendor_hold
    case = await case_of(world)
    assert case["state"] == "needs_review"
    hold_cmd = await one(world.pool, "select attempts, status from app.outbox where case_id = %s and command = 'place_hold'", case["id"])
    assert (hold_cmd["attempts"], hold_cmd["status"]) == (5, "failed")
    alert = await one(world.pool, "select kind, severity from app.alerts where case_id = %s", case["id"])
    assert (alert["kind"], alert["severity"]) == ("hold_not_confirmed", "critical")


async def test_a_slow_read_back_is_waited_for(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.mock_settings(hold_readback_lag_ms=300)
    world.worker._sleep = _real_sleep  # the read-back has to actually wait
    await change_and_detect(world)
    assert (await case_of(world))["state"] == "calling"


async def _real_sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


async def test_call_reports_are_authenticated_validated_and_never_echo_digits(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    call_id = world.voice.placed[0]["call_id"]

    assert (await report(world, case["id"], call_id, token=b"wrong-token-wrong-token-xx")).status_code == 401
    assert (await report(world, case["id"], call_id, key=False)).status_code == 422
    bad = await report(world, case["id"], call_id, answers=transit(call_id, digits="12345"))
    assert bad.status_code == 422 and "12345" not in bad.text
    other = await report(world, case["id"], uuid.uuid4())
    assert other.status_code == 409 and other.json()["code"] == "not_current_call"

    assert (await report(world, case["id"], call_id)).status_code == 204
    replay = await report(world, case["id"], call_id)
    assert replay.status_code == 200 and replay.json() == {"status": "already_applied"}


async def test_a_report_from_before_a_re_hold_is_refused(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    old_call = world.voice.placed[0]["call_id"]

    await change_and_detect(world, account_number="000077778888")  # a second change mid-call
    case = await case_of(world)
    assert (case["state"], case["version"]) == ("calling", 2)
    new = world.voice.placed[-1]
    assert new["key"] == f"{case['id']}:call:v2:1", "the new generation's call has its own key"

    assert (await report(world, case["id"], old_call)).status_code == 409
    assert (await report(world, case["id"], new["call_id"], answers=transit(new["call_id"], digits="8888"))).status_code == 204
    await world.worker.drain(world.dataset.id)
    assert (await case_of(world))["outcome"] == "verified"


async def test_no_answer_retries_inside_the_calling_window_on_the_lab_clock(pool):
    start = dt.datetime(2026, 11, 3, 16, 0, tzinfo=dt.UTC)  # a Tuesday, 08:00 in Los Angeles: before the window
    world = await make_world(pool, kind="lab", sim_now=start, settings=core_settings(calling_window="09:00-16:30", calling_days="mon-fri"))
    try:
        await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
        await change_and_detect(world)
        assert world.voice.placed == [], "no call before 09:00 vendor time"
        cmd = await one(pool, "select run_after from app.outbox where dataset_id = %s and command = 'place_call'", world.dataset.id)
        assert cmd["run_after"] == dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.UTC)

        await _advance(pool, world, dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.UTC))
        assert len(world.voice.placed) == 1
        case = await case_of(world)
        assert (await report(world, case["id"], world.voice.placed[0]["call_id"], result="no_answer")).status_code == 204
        assert (await case_of(world))["state"] == "retry_wait"

        await _advance(pool, world, dt.datetime(2026, 11, 3, 18, 59, tzinfo=dt.UTC))
        assert len(world.voice.placed) == 1, "the retry waits two simulated hours"
        await _advance(pool, world, dt.datetime(2026, 11, 3, 19, 0, tzinfo=dt.UTC))
        assert [p["attempt"] for p in world.voice.placed] == [1, 2]
    finally:
        await world.core.aclose()
        await world.mock.aclose()


async def _advance(pool, world, to: dt.datetime) -> None:
    async with system_tx(pool) as conn:
        await conn.execute("select app.advance_clock(%s, %s)", (world.dataset.id, to))
    await world.worker.drain(world.dataset.id)


async def test_without_the_ledger_stub_no_number_is_provable_and_a_person_takes_over(world):
    world.worker.resolver = LedgerResolver(ResolverMode.STUB)  # the real resolver; Inesh's stub isn't in the package yet
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    assert case["state"] == "needs_review" and world.voice.placed == []
    alert = await one(world.pool, "select kind from app.alerts where case_id = %s", case["id"])
    assert alert["kind"] == "no_provable_number"


async def test_a_pre_dial_refusal_sends_the_case_to_a_person(world):
    world.voice.refuse = "not_on_dial_allowlist"
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    assert case["state"] == "needs_review"
    alert = await one(world.pool, "select kind, message from app.alerts where case_id = %s", case["id"])
    assert alert["kind"] == "command_stuck" and "place_call" in alert["message"]


async def test_a_release_that_races_a_new_change_puts_the_hold_back(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    await report(world, case["id"], world.voice.placed[0]["call_id"])
    await world.worker.drain(world.dataset.id)
    await release(world, case["id"], 1)

    # A second change lands in Ramp; Dialback detects it in the instant between releasing the
    # hold in Ramp and confirming the release.
    await world.mock_settings(silent_changes=True)
    await world.bank_change(account_number="000099990000")
    ramp = world.ramp
    real_release = ramp.release_vendor_hold

    async def release_then_detect(vendor_id: str) -> None:
        await real_release(vendor_id)
        await sync_vendor(world.pool, ramp, world.keys, world.dataset.id, vendor_id, "poll")

    ramp.release_vendor_hold = release_then_detect
    try:
        await world.worker.run_commands(world.dataset.id, limit=1)
    finally:
        del ramp.release_vendor_hold

    assert (await ramp.get_vendor(world.refs["acme"])).hold_id is not None, "the hold is back on"
    alert = await one(world.pool, "select kind from app.alerts where case_id = %s and kind = 'vendor_not_held'", case["id"])
    assert alert is not None
    case = await case_of(world)
    assert (case["state"], case["version"]) == ("held", 2)


# What the decision engine reads (spec 0005) -------------------------------------------------------

@pytest.mark.parametrize("dialed", [None, uuid.UUID("0f0f0f0f-0000-4000-8000-000000000000")], ids=["missing", "another"])
async def test_a_report_without_the_picked_contact_point_is_not_eligible(world, dialed):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    assert (await report(world, case["id"], world.voice.placed[0]["call_id"], contact_point=dialed)).status_code == 204
    await world.worker.drain(world.dataset.id)
    decision = await one(world.pool, "select outcome, rules_fired from app.decisions where case_id = %s", case["id"])
    assert (decision["outcome"], list(decision["rules_fired"])) == ("needs_review", ["NR99_nothing_decided"])


async def test_a_dropped_call_that_said_no_still_counts(world, monkeypatch):
    import dialback_core.worker as worker_module
    seen, real = [], worker_module.decide
    monkeypatch.setattr(worker_module, "decide", lambda facts: seen.append(facts) or real(facts))
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)

    first = world.voice.placed[0]["call_id"]
    dropped = await report(world, case["id"], first, result="failed", answers=transit(first, change="no"),
                           retry_at="2026-01-01T00:00:00Z")
    assert dropped.status_code == 204 and (await case_of(world))["state"] == "retry_wait"
    await world.worker.drain(world.dataset.id)  # the retry is already due
    assert [p["attempt"] for p in world.voice.placed] == [1, 2]
    assert (await report(world, case["id"], world.voice.placed[1]["call_id"], attempt=2)).status_code == 204
    await world.worker.drain(world.dataset.id)

    [facts] = seen
    assert facts.change_requested == ("yes",), "the redial's own answers are the ones being decided"
    assert [(c.result, c.change_requested) for c in facts.earlier_calls] == [("failed", ("no",))]
    assert facts.dialed_eligible_number and facts.same_person_yes_and_digits and facts.consent_first
    assert world.change_context.calls[-1]["speaker_names"] == ("Jordan Lee",)
    case = await case_of(world)
    assert (case["state"], case["outcome"]) == ("blocked", "not_verified"), "the dropped call's no still decides"


async def test_only_this_generations_pick_and_calls_count(world, monkeypatch):
    import dialback_core.worker as worker_module
    seen, real = [], worker_module.decide
    monkeypatch.setattr(worker_module, "decide", lambda facts: seen.append(facts) or real(facts))
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    first = world.voice.placed[0]["call_id"]
    dropped = await report(world, case["id"], first, result="failed", answers=transit(first, change="no"),
                           retry_at="2030-01-01T00:00:00Z")
    assert dropped.status_code == 204

    world.resolver.point = uuid.UUID("0f0f0f0f-1111-4000-8000-000000000002")  # the ledger picks anew for v2
    await change_and_detect(world, account_number="000077778888")
    case = await case_of(world)
    assert (case["version"], case["state"]) == (2, "calling")
    second = world.voice.placed[-1]["call_id"]
    v2 = await report(world, case["id"], second, attempt=case["call_attempts"], answers=transit(second, digits="8888"),
                      contact_point=world.resolver.point)
    assert v2.status_code == 204
    await world.worker.drain(world.dataset.id)
    [facts] = seen
    assert facts.dialed_eligible_number, "v2's own pick"
    assert facts.earlier_calls == (), "version 1's call (and its no) belongs to another change"


async def test_a_change_context_failure_goes_to_a_person(world):
    world.change_context.facts = RuntimeError("ledger down")
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    assert (await report(world, case["id"], world.voice.placed[0]["call_id"])).status_code == 204
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert case["state"] == "needs_review" and case["outcome"] is None
    alert = await one(world.pool, "select kind, message from app.alerts where case_id = %s", case["id"])
    assert alert["kind"] == "command_stuck" and "decide" in alert["message"]


async def test_every_company_answer_must_match(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    call_id = world.voice.placed[0]["call_id"]
    answers = transit(call_id)
    answers["answers"].append(answer("company_named", "Globex Corporation", 11))
    assert (await report(world, case["id"], call_id, answers=answers)).status_code == 204
    await world.worker.drain(world.dataset.id)
    decision = await one(world.pool, "select outcome, rules_fired from app.decisions where case_id = %s", case["id"])
    assert decision["outcome"] == "needs_review" and "NR03_wrong_company" in decision["rules_fired"]


async def test_without_the_ledgers_change_context_every_answer_is_unknown(world):
    facts = await LedgerChangeContext().check(case_id=uuid.uuid4(), dataset_id=world.dataset.id, vendor_id=uuid.uuid4(),
                                              cutoff=dt.datetime(2026, 11, 3, tzinfo=dt.UTC), speaker_names=("Jordan Lee",),
                                              offered_numbers=())
    assert facts == ChangeFacts()


async def test_the_ledgers_change_context_counts_only_booleans(world, monkeypatch):
    import dialback_ledger
    monkeypatch.setattr(dialback_ledger, "change_context", raising=False, value=lambda **_: {
        "speaker_in_change_thread": "no", "offered_number_from_request": True, "speaker_known_before_request": None,
        "something_else": True})
    facts = await LedgerChangeContext().check(case_id=uuid.uuid4(), dataset_id=world.dataset.id, vendor_id=uuid.uuid4(),
                                              cutoff=dt.datetime(2026, 11, 3, tzinfo=dt.UTC), speaker_names=(),
                                              offered_numbers=("415 555 0177",))
    assert facts == ChangeFacts(offered_number_from_request=True), "a string 'no' is not False"


def test_every_company_answer_must_match_and_an_unreadable_one_is_unknown():
    from dialback_core.company import every_name_matches
    assert every_name_matches(["Acme Fasteners"], "Acme Fasteners LLC") is True
    assert every_name_matches(["Acme Fasteners", "Globex"], "Acme Fasteners LLC") is False
    assert every_name_matches(["Acme Fasteners", "LLC"], "Acme Fasteners LLC") is None, "'LLC' alone names nobody"
    assert every_name_matches([], "Acme Fasteners LLC") is None
