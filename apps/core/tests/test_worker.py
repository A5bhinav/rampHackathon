"""The walking skeleton's money path, end to end in process: a bank change in the Ramp mock
becomes a case, the hold goes on, a call is placed and reported, code decides, a different
person releases, and the bills get their dates back. Plus the ways it fails closed."""
from __future__ import annotations

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import psycopg
import pytest
from conftest import core_settings, make_world
from contract import CONTRACTS
from dialback_core.calling_window import DAYS, CallingWindow
from dialback_core.db import system_tx
from dialback_core.detection import import_all, sync_vendor
from dialback_core.flags import ResolverMode
from dialback_core.ports import ChangeFacts, LedgerChangeContext, LedgerResolver
from dialback_core.ramp import RampError
from dialback_core.worker import Worker
from dialback_decision import rules
from flows import (
    _types,
    add_vendor,
    answer,
    bills,
    case_of,
    change_and_detect,
    one,
    pay,
    release,
    report,
    transit,
)

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
    assert list(decision["rules_fired"]) == [r.id for r in rules.VERIFIED], "every condition checked (spec 0006)"

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


@pytest.mark.parametrize("digits, last_four, outcome, state", [
    ("6666", "match", "verified", "awaiting_release"),
    ("1111", "mismatch", "not_verified", "blocked"),
], ids=["new-account-digits", "other-digits"])
async def test_a_first_account_case_walks_the_money_path(world, digits, last_four, outcome, state):
    """Spec 0050: a vendor paid by check gets its first bank account. There is no previous account,
    and the case takes the same path as any other."""
    await add_vendor(world, "birch", "Birch Supply Co",
                     bills=[{"ref": "birch-bill", "invoice_number": "B-1", "amount_cents": 50000, "payment_date": "2026-11-06"}])
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.bank_change(vendor_ref="birch")
    await world.deliver_webhooks()
    await world.worker.drain(world.dataset.id)

    case = await case_of(world)
    assert case is not None, "a known vendor's first account opens a case"
    assert case["state"] == "calling" and case["call_attempts"] == 1
    [bill] = await world.ramp.list_bills_for_vendor(world.refs["birch"])
    assert bill.is_on_hold and bill.payment_date is None, "the hold went on and cleared the date"
    assert world.refs["birch-bill"] not in await pay(world, "2026-12-31"), "R1: nothing is paid while the case is open"
    [placed] = world.voice.placed
    assert placed["case_id"] == case["id"]

    answers = transit(placed["call_id"], digits=digits, company="Birch Supply")
    assert (await report(world, case["id"], placed["call_id"], answers=answers)).status_code == 204
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert (case["state"], case["outcome"]) == (state, outcome)
    decision = await one(world.pool, "select last_four_result from app.decisions where case_id = %s", case["id"])
    assert decision["last_four_result"] == last_four, "no old account, so the digits match the new one or nothing"

    chain = await one(world.pool, "select * from audit.verify_chain(%s)", world.dataset.chain_id)
    assert chain["ok"], chain["detail"]
    assert [r["type"] for r in await _types(world, case["id"])] == [
        "change_detected", "hold_confirmed", "number_resolved", "call_started", "call_ended", "decision_made"]


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


async def test_the_ledger_stub_picks_its_contract_example(world):
    world.worker.resolver = LedgerResolver(ResolverMode.STUB)  # the real resolver calling Inesh's stub (spec 0018)
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    resolved = await one(world.pool, "select body_jcs::jsonb -> 'body' as body from audit.evidence_events "
                                     "where case_id = %s and type = 'number_resolved'", case["id"])
    assert resolved["body"]["rule_version"] == "ledger-stub-v0"
    assert resolved["body"]["contact_point_id"] == "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"
    assert case["state"] == "calling" and len(world.voice.placed) == 1, "the stub's pick goes on to the call"
    hold = await one(world.pool, "select status::text as status from app.holds where case_id = %s", case["id"])
    assert hold["status"] == "confirmed", "the hold stays on while the call is made"


async def test_without_the_ledger_rule_no_number_is_provable_and_a_person_takes_over(world):
    world.worker.resolver = LedgerResolver(ResolverMode.LEDGER)  # the real resolver; ledger-select-v1 isn't in the package yet
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    assert case["state"] == "needs_review" and world.voice.placed == []
    alert = await one(world.pool, "select kind from app.alerts where case_id = %s", case["id"])
    assert alert["kind"] == "no_provable_number"


def _page(*lines: str) -> list[dict]:
    return [{"width": 612, "height": 792, "spans": [{"text": t, "x": 72, "y": 72 + 14 * i, "size": 10, "color": "#000000"}
                                                     for i, t in enumerate(lines)]}]


def _source(doc_id: str, channel: str, received: dt.datetime, *lines: str, **custody) -> dict:
    """A source document in the corpus's shape, for the ledger's ingest_all."""
    return {"id": doc_id, "kind": channel, "claimed_date": None, "header_date": None, "scan": False, "pages": _page(*lines),
            "custody": {"channel": channel, "parent_document_id": None, "received_at": received.isoformat(),
                        "sender": custody.get("sender"), "subject": custody.get("subject"), "thread_id": custody.get("thread")}}


async def test_withheld_selection_goes_to_review(world, database_url, monkeypatch):
    # Spec 0079, decision 5: the core needs no change for withheld. The real ledger rule runs on
    # FINDINGS A3 (an old email in the change request's thread has the vendor's line, older than its
    # other evidence; a planted number is provable), withholds, and the core treats the selection
    # like no_provable_number: no call, its reason, and a person reviews.
    from dialback_core.timeline import summary
    from dialback_ledger.ingest import ingest_all
    from dialback_ledger.reader import StubReader
    from dialback_ledger.select import WITHHELD, ChangeRequest, select
    from dialback_ledger.store import add_vendor_domain, mark_change_request, vendor_rows, write_ingest
    monkeypatch.delenv("DIALBACK_READER", raising=False)
    monkeypatch.delenv("DIALBACK_CONTEXT_GUARD", raising=False)
    monkeypatch.delenv("DIALBACK_LABEL_BAR", raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)             # select_number opens its own connection
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    vendor = (await one(world.pool, "select id from app.vendors where dataset_id = %s and ramp_vendor_id = %s",
                        world.dataset.id, world.refs["acme"]))["id"]
    now = dt.datetime.now(dt.UTC)

    def ago(days: int) -> dt.datetime:
        return now - dt.timedelta(days=days)

    sources = [
        _source("real-contract", "contract", ago(200), "Master supply agreement: Acme", "Accounts receivable: (415) 555-0142"),
        _source("real-listing", "listing", ago(200), "Acme Fasteners", "Accounts receivable: (415) 555-0142"),
        _source("planted-contract", "contract", ago(150), "Supply agreement: Acme", "Accounts receivable: (645) 555-0177"),
        _source("planted-listing", "listing", ago(150), "Acme Fasteners, west office", "Accounts receivable: (645) 555-0177"),
        _source("old-copy", "email", ago(365), "Accounts receivable: (415) 555-0142", sender="ar@acme.example",
                thread="thr-request", subject="Updated banking details"),
        _source("request", "email", now - dt.timedelta(hours=1), "Please update our bank details for future payments.",
                sender="ar@acme.example", thread="thr-request", subject="Updated banking details")]
    async with await psycopg.AsyncConnection.connect(database_url) as conn, conn.transaction():
        await write_ingest(conn, dataset_id=world.dataset.id, vendor_id=vendor,
                           result=ingest_all(sources, extractor_version="test", reader=StubReader()))
        await add_vendor_domain(conn, dataset_id=world.dataset.id, vendor_id=vendor, domain="acme.example")
        await mark_change_request(conn, dataset_id=world.dataset.id, vendor_id=vendor, document_id="request")
    async with world.pool.connection() as conn:   # the premise: with both flags on, the rule withholds on this world
        at = dt.datetime.now(dt.UTC)
        rows = await vendor_rows(conn, dataset_id=world.dataset.id, vendor_id=vendor, before=at)
    req = await one(world.pool, "select d.id, d.received_at, d.thread_id, d.subject, d.sender from ledger.change_requests r "
                                "join ledger.documents d on d.id = r.document_id where r.dataset_id = %s and r.vendor_id = %s",
                    world.dataset.id, vendor)
    premise = select(case_id=uuid.UUID(int=0), dataset_id=world.dataset.id, vendor_id=vendor, requested_cutoff=at, case_opened_at=at,
                     request=ChangeRequest(str(req["id"]), req["received_at"], req["thread_id"], req["subject"], req["sender"]), **rows)
    assert premise["outcome"] == "withheld"
    world.worker.resolver = LedgerResolver(ResolverMode.LEDGER)  # the real resolver and the real rule
    await change_and_detect(world)

    case = await case_of(world)
    assert case["state"] == "needs_review" and world.voice.placed == [], "nobody is called"
    alert = await one(world.pool, "select kind from app.alerts where case_id = %s", case["id"])
    assert alert["kind"] == "no_provable_number"
    event = await one(world.pool, "select body_jcs::jsonb -> 'body' as body from audit.evidence_events "
                                  "where case_id = %s and type = 'no_provable_number'", case["id"])
    assert event["body"]["reason"] == WITHHELD
    stored = await one(world.pool, "select outcome::text as outcome, contact_point_id, rule_version from ledger.number_selections "
                                   "where id = %s", event["body"]["number_selection_id"])
    assert stored["outcome"] == "withheld" and stored["contact_point_id"] is None
    assert event["body"]["rule_version"] == stored["rule_version"]
    # The case timeline says why, not "no number is provable".
    assert summary("no_provable_number", event["body"], "system") == (
        "The change request would change which phone number is called, so no call was made and a person must verify.")
    assert summary("no_provable_number", {"reason": "No number met the rule."}, "system").startswith(
        "No phone number for this vendor is provable")


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
    assert (decision["outcome"], list(decision["rules_fired"])) == \
        ("needs_review", ["NR99_conditions_not_met", "V01_number_eligible_unmet"])


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


# The clean call's decision, which test_a_bank_change_walks_all_the_way_to_release pins too.
WITHOUT_THE_EXTRA_ANSWER = ("verified", [r.id for r in rules.VERIFIED])


@pytest.mark.parametrize("extra", [None, "Globex Corporation"], ids=["without", "with-globex"])
async def test_another_company_named_decides_as_the_call_would_without_it(world, extra):
    """Spec 0082, D7b: another company named with no refusal word blocks nothing, so the same call
    decides the same with or without it. The person who releases sees the answer."""
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    call_id = world.voice.placed[0]["call_id"]
    answers = transit(call_id)
    if extra:
        answers["answers"].append(answer("company_named", extra, 11))
    assert (await report(world, case["id"], call_id, answers=answers)).status_code == 204
    await world.worker.drain(world.dataset.id)
    decision = await one(world.pool, "select outcome, rules_fired from app.decisions where case_id = %s", case["id"])
    assert "NR03_wrong_company" not in decision["rules_fired"]
    assert (decision["outcome"], list(decision["rules_fired"])) == WITHOUT_THE_EXTRA_ANSWER


async def test_a_refusal_in_the_company_answer_goes_to_review(world):
    """Spec 0082, D7a and D7b: a refusal word is the one company answer that still blocks (NR03)."""
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    call_id = world.voice.placed[0]["call_id"]
    answers = transit(call_id, company="Not Acme Fasteners, this is Globex")
    assert (await report(world, case["id"], call_id, answers=answers)).status_code == 204
    await world.worker.drain(world.dataset.id)
    decision = await one(world.pool, "select outcome, rules_fired from app.decisions where case_id = %s", case["id"])
    assert (decision["outcome"], list(decision["rules_fired"])) == ("needs_review", ["NR03_wrong_company"])
    assert (await case_of(world))["state"] == "needs_review"


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


def test_a_refusal_in_any_company_answer_is_false_and_another_or_unreadable_one_is_unknown():
    from dialback_core.company import every_name_matches
    assert every_name_matches(["Acme Fasteners"], "Acme Fasteners LLC") is True
    assert every_name_matches(["Acme Fasteners", "Globex"], "Acme Fasteners LLC") is None, "spec 0082, D7b"
    assert every_name_matches(["Acme Fasteners", "Not Acme, this is Globex"], "Acme Fasteners LLC") is False
    assert every_name_matches(["Acme Fasteners", "LLC"], "Acme Fasteners LLC") is None, "'LLC' alone names nobody"
    assert every_name_matches([], "Acme Fasteners LLC") is None


@pytest.mark.parametrize("telephony, expected", [
    ({"direction": "outbound", "dialed_number_is_pick": True, "answered_by": "human"}, (True, True, "human")),
    ({"direction": "inbound", "dialed_number_is_pick": False, "answered_by": "machine"}, (False, False, "machine")),
    ({"direction": "outbound", "dialed_number_is_pick": False}, (True, False, None)),
    ({"direction": "inbound", "dialed_number_is_pick": True, "answered_by": "human"}, (False, True, "human")),
    ({}, (None, None, None)),
], ids=["placed", "suspect", "wrong-number", "inbound", "older-report"])
async def test_the_voice_reports_telephony_facts(world, monkeypatch, telephony, expected):
    """Spec 0005, D6: the call's direction, the adapter's dialed-number check and who answered
    come from the voice report (contract: A5bhinav/rampHackathon#13). Missing means unknown."""
    import json

    import dialback_core.worker as worker_module
    from jsonschema import Draft202012Validator
    seen, real = [], worker_module.decide
    monkeypatch.setattr(worker_module, "decide", lambda facts: seen.append(facts) or real(facts))
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    assert (await report(world, case["id"], world.voice.placed[0]["call_id"], telephony=telephony)).status_code == 204
    await world.worker.drain(world.dataset.id)
    [facts] = seen
    assert (facts.dialed_out, facts.dialed_number_is_pick, facts.answered_by) == expected

    stored = await one(world.pool, "select body_jcs::jsonb -> 'body' as body from audit.evidence_events "
                                   "where case_id = %s and type = 'call_ended'", case["id"])
    schema = json.loads((CONTRACTS / "schemas" / "evidence-event.schema.json").read_text())
    body = {k: v for k, v in stored["body"].items() if k != "answers"}  # answers are Sami's schema, not on main yet
    assert not list(Draft202012Validator({"$defs": schema["$defs"], **schema["$defs"]["CallEndedBody"]}).iter_errors(body))


@pytest.mark.parametrize("telephony", [{"dialed_number_is_pick": "yes"}, {"dialed_number_is_pick": 1},
                                       {"direction": "sideways"}, {"answered_by": "fax"}],
                         ids=["pick-yes", "pick-1", "direction", "answered-by"])
async def test_a_telephony_fact_that_isnt_in_the_contract_is_refused(world, telephony):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    r = await report(world, case["id"], world.voice.placed[0]["call_id"], telephony=telephony)
    assert r.status_code == 422 and "6666" not in r.text, "refused, and the body is never echoed"
    assert (await case_of(world))["state"] == "calling", "nothing was recorded"


@pytest.mark.parametrize("slot, value", [("recording_consent", "No"), ("change_requested", "maybe"), ("flag", "approve_now")],
                         ids=["consent", "change", "flag"])
async def test_an_answer_value_outside_the_contract_is_refused_on_arrival(world, slot, value):
    """A value the engine would reject (NR97) is refused when the report arrives, so it can't sit in
    the evidence and poison later decisions through earlier_calls."""
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    call_id = world.voice.placed[0]["call_id"]
    answers = transit(call_id)
    answers["answers"].append(answer(slot, value, 11))
    r = await report(world, case["id"], call_id, answers=answers)
    assert r.status_code == 422 and "6666" not in r.text
    assert (await case_of(world))["state"] == "calling"


# Callbacks, deadlines (spec 0007) ------------------------------------------------------------------

LAB_START = dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.UTC)  # a Tuesday, 09:00 in Los Angeles


async def lab_world(pool):
    return await make_world(pool, kind="lab", sim_now=LAB_START,
                            settings=core_settings(calling_window="09:00-16:30", calling_days="mon-fri"))


async def test_a_callback_without_a_time_is_refused(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    r = await report(world, case["id"], world.voice.placed[0]["call_id"], result="callback_requested")
    assert r.status_code == 422 and "retry_at" in r.text
    assert (await case_of(world))["state"] == "calling"


async def test_a_callback_is_redialed_at_the_time_asked_and_inside_the_window(pool):
    world = await lab_world(pool)
    try:
        await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
        await change_and_detect(world)
        case = await case_of(world)
        first = world.voice.placed[0]["call_id"]
        consent = {"schema_version": "call-answers/v0", "call_id": str(first), "answers": [answer("recording_consent", "yes", 1)]}
        # "Call me back at five": 17:00 in Los Angeles, after the window closes at 16:30.
        asked = dt.datetime(2026, 11, 4, 1, 0, tzinfo=dt.UTC)
        r = await report(world, case["id"], first, result="callback_requested", answers=consent, retry_at=asked.isoformat())
        assert r.status_code == 204 and (await case_of(world))["state"] == "retry_wait"
        timer = await one(pool, "select fire_at from app.timers where case_id = %s and kind = 'retry_call' and status = 'scheduled'",
                          case["id"])
        assert timer["fire_at"] == asked, "the time the vendor asked for, not the default two hours"

        await _advance(pool, world, asked - dt.timedelta(minutes=1))
        assert len(world.voice.placed) == 1, "not before the time asked"
        await _advance(pool, world, asked)
        assert len(world.voice.placed) == 1, "the time asked is outside the window: wait for it to open"
        await _advance(pool, world, dt.datetime(2026, 11, 4, 17, 0, tzinfo=dt.UTC))  # Wednesday 09:00 in Los Angeles
        assert [p["attempt"] for p in world.voice.placed] == [1, 2]

        assert (await report(world, case["id"], world.voice.placed[1]["call_id"], attempt=2)).status_code == 204
        await world.worker.drain(world.dataset.id)
        case = await case_of(world)
        assert (case["state"], case["outcome"]) == ("awaiting_release", "verified"), "the earlier callback call is valid input"
    finally:
        await world.core.aclose()
        await world.mock.aclose()


async def test_the_deadline_survives_a_re_hold_and_overtakes_a_callback(pool):
    world = await lab_world(pool)
    try:
        await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
        await change_and_detect(world)
        case = await case_of(world)
        deadline = case["deadline_at"]
        assert deadline == dt.datetime(2026, 11, 6, 17, 0, tzinfo=dt.UTC), "three business days"

        await _advance(pool, world, LAB_START + dt.timedelta(hours=1))
        await change_and_detect(world, account_number="000077778888")  # a second change re-holds the case
        case = await case_of(world)
        assert case["version"] == 2 and case["deadline_at"] == deadline, "a new change doesn't buy more time"
        timer = await one(pool, "select fire_at from app.timers where case_id = %s and kind = 'deadline' and status = 'scheduled'",
                          case["id"])
        assert timer["fire_at"] == deadline

        later = deadline + dt.timedelta(days=3)  # Monday 09:00 in Los Angeles: inside the window, after the deadline
        r = await report(world, case["id"], world.voice.placed[-1]["call_id"], attempt=case["call_attempts"],
                         result="callback_requested", retry_at=later.isoformat())
        assert r.status_code == 204
        await _advance(pool, world, deadline)
        case = await case_of(world)
        assert case["state"] == "needs_review", "the deadline overtakes the callback"
        alert = await one(pool, "select kind from app.alerts where case_id = %s and kind = 'deadline_reached'", case["id"])
        assert alert is not None
        calls = len(world.voice.placed)
        await _advance(pool, world, later)
        assert len(world.voice.placed) == calls and (await case_of(world))["state"] == "needs_review", "no callback after it"
    finally:
        await world.core.aclose()
        await world.mock.aclose()


async def test_the_deadline_leaves_a_case_a_person_owns_alone(pool):
    world = await lab_world(pool)
    try:
        await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
        await change_and_detect(world)
        case = await case_of(world)
        assert (await report(world, case["id"], world.voice.placed[0]["call_id"])).status_code == 204
        await world.worker.drain(world.dataset.id)
        assert (await case_of(world))["state"] == "awaiting_release"
        await _advance(pool, world, case["deadline_at"] + dt.timedelta(minutes=1))
        assert (await case_of(world))["state"] == "awaiting_release", "an approver owns it now"
        assert await one(pool, "select 1 from app.alerts where case_id = %s and kind = 'deadline_reached'", case["id"]) is None

        # A new change hands the case back to code after its deadline: the next check sends it to a person.
        await change_and_detect(world, account_number="000077778888")
        assert (await case_of(world))["state"] == "calling"
        await _advance(pool, world, case["deadline_at"] + dt.timedelta(hours=1, minutes=1))
        assert (await case_of(world))["state"] == "needs_review", "the deadline wasn't used up while a person owned the case"
    finally:
        await world.core.aclose()
        await world.mock.aclose()


async def test_a_callback_inside_the_window_is_redialed_exactly_then(pool):
    world = await lab_world(pool)
    try:
        await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
        await change_and_detect(world)
        case = await case_of(world)
        asked = LAB_START + dt.timedelta(hours=3)  # noon in Los Angeles
        r = await report(world, case["id"], world.voice.placed[0]["call_id"], result="callback_requested", retry_at=asked.isoformat())
        assert r.status_code == 204
        await _advance(pool, world, asked - dt.timedelta(seconds=1))
        assert len(world.voice.placed) == 1
        await _advance(pool, world, asked)
        assert [p["attempt"] for p in world.voice.placed] == [1, 2]
    finally:
        await world.core.aclose()
        await world.mock.aclose()


@pytest.mark.parametrize("when", ["infinity", "soon", "2026-11-03T10:00:00"], ids=["infinity", "words", "no-offset"])
async def test_a_callback_time_that_isnt_a_moment_is_refused(world, when):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    r = await report(world, case["id"], world.voice.placed[0]["call_id"], result="callback_requested", retry_at=when)
    assert r.status_code == 422 and (await case_of(world))["state"] == "calling"


# Demo calling hours (spec 0011, D8) ----------------------------------------------------------------

async def test_demo_datasets_call_outside_the_normal_window(pool):
    """The normal window is closed all day today (it opens on another weekday only). A demo
    dataset calls anyway, within DIALBACK_DEMO_CALLING_WINDOW (default: any time), while a dev
    dataset waits for the window."""
    today = dt.datetime.now(ZoneInfo("America/Los_Angeles")).weekday()
    settings = core_settings(calling_window="09:00-16:30", calling_days=DAYS[(today + 3) % 7])
    dev, demo = await make_world(pool, settings=settings), await make_world(pool, kind="demo", settings=settings)
    try:
        for w in (dev, demo):
            await import_all(w.pool, w.ramp, w.keys, w.dataset.id)
            await change_and_detect(w)
            assert (await case_of(w))["state"] == "calling"
        assert dev.voice.placed == [], "a dev dataset waits for the window"
        assert len(demo.voice.placed) == 1, "a demo dataset calls now"

        # The retry timer takes the same window: once due, it redials now rather than waiting days.
        case = await case_of(demo)
        assert (await report(demo, case["id"], demo.voice.placed[0]["call_id"], result="no_answer")).status_code == 204
        async with system_tx(pool) as conn:  # the retry's back-off has passed
            await conn.execute("update app.timers set fire_at = now() where case_id = %s and kind = 'retry_call' "
                               "and status = 'scheduled'", (case["id"],))
        await demo.worker.drain(demo.dataset.id)
        assert [p["attempt"] for p in demo.voice.placed] == [1, 2]
    finally:
        for w in (dev, demo):
            await w.core.aclose()
            await w.mock.aclose()


def test_the_demo_calling_window_setting():
    always = CallingWindow.parse_spec("America/Los_Angeles", "always")
    assert always.contains(dt.datetime(2026, 11, 8, 23, 59, 59, tzinfo=dt.UTC))
    assert always.contains(dt.datetime(2026, 11, 9, 7, 59, 59, tzinfo=dt.UTC))  # 23:59:59 in Los Angeles
    evening = CallingWindow.parse_spec("America/Los_Angeles", "17:00-22:00 all")
    assert evening.contains(dt.datetime(2026, 11, 20, 5, 0, tzinfo=dt.UTC))  # Thu 21:00 in Los Angeles
    assert not evening.contains(dt.datetime(2026, 11, 19, 20, 0, tzinfo=dt.UTC))  # Thu 12:00
    weekend = CallingWindow.parse_spec("America/Los_Angeles", "17:00-22:00 fri-mon")  # a range that wraps the week
    assert weekend.days == frozenset({4, 5, 6, 0})
    for bad in ("whenever", "22:00-17:00", "17:00-22:00 someday", "17:00-22:00 ,"):
        with pytest.raises(ValueError):
            CallingWindow.parse_spec("America/Los_Angeles", bad)


async def test_an_unreadable_demo_window_stops_the_worker_at_startup(world):
    with pytest.raises(ValueError, match="not a calling window"):
        Worker(world.pool, core_settings(demo_calling_window="evenings"), world.connector, world.resolver, world.voice)
