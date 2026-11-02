"""The walking skeleton's money path, end to end in process: a bank change in the Ramp mock
becomes a case, the hold goes on, a call is placed and reported, code decides, a different
person releases, and the bills get their dates back. Plus the ways it fails closed."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from conftest import INTERNAL_TOKEN, core_settings, make_world
from dialback_core.db import person_tx, system_tx
from dialback_core.detection import import_all, sync_vendor
from dialback_core.flags import ResolverMode
from dialback_core.ports import LedgerResolver
from dialback_core.ramp import RampError

pytestmark = pytest.mark.anyio

CLEAN_CHECKS = {"disclosure_first": True, "answers_grounded": True, "agent_spoke_account_digits": False,
                "new_number_accepted": False, "policy_violations": 0, "speech_guard_trips": 0}


async def one(pool, sql: str, *args):
    async with system_tx(pool) as conn:
        return await (await conn.execute(sql, args)).fetchone()


async def case_of(world):
    return await one(world.pool, "select * from app.cases where dataset_id = %s order by opened_at desc limit 1", world.dataset.id)


async def change_and_detect(world, account_number="000055556666", actor="clerk"):
    await world.bank_change(account_number=account_number, actor=actor)
    await world.deliver_webhooks()
    await world.worker.drain(world.dataset.id)


def transit(call_id, digits="6666", company="Acme Fasteners", change="yes"):
    def a(slot, value, turn):
        return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"tc_{turn}",
                "captured_at": "2026-11-03T17:02:11Z", "capture_method": "keypad" if slot == "account_last4" else "speech"}
    return {"schema_version": "call-answers/v0", "call_id": str(call_id), "answers": [
        a("recording_consent", "yes", 1), a("company_named", company, 3), a("speaker_name", "Jordan Lee", 5),
        a("change_requested", change, 7), a("account_last4", digits, 9)]}


async def report(world, case_id, call_id, attempt=1, result="answers_recorded", answers=None, token=INTERNAL_TOKEN, key=True):
    body = {"call_id": str(call_id), "case_id": str(case_id), "result": result, "attempt": attempt,
            "agent_version": "agent-test", "checks": CLEAN_CHECKS}
    if result == "answers_recorded":
        body["answers"] = answers or transit(call_id)
    headers = {"Authorization": f"Bearer {token.decode()}"}
    if key:
        headers["Idempotency-Key"] = f"{case_id}:call:{attempt}:ended"
    return await world.core.post(f"/internal/calls/{call_id}/ended", json=body, headers=headers)


async def release(world, case_id, version, who="approver1"):
    async with person_tx(world.pool, world.users[who], {}) as conn:
        return (await (await conn.execute("select app.release_case(%s, %s, 'approve', %s) as r",
                                          (case_id, version, "Confirmed on the ledger's number; digits match."))).fetchone())["r"]


async def bills(world):
    return {b.id: b for b in await world.ramp.list_bills_for_vendor(world.refs["acme"])}


async def pay(world, as_of: str) -> list[str]:
    r = await world.mock.post("/_mock/payment-run", json={"as_of": as_of}, headers=world.auth)
    return r.json()["paid_bill_ids"]


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
    assert decision["last_four_result"] == "match" and "U01_speaker_thread_not_checked" in decision["rules_fired"]

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


async def _types(world, case_id):
    async with system_tx(world.pool) as conn:
        return await (await conn.execute("select type from audit.evidence_events where case_id = %s order by seq", (case_id,))).fetchall()


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
