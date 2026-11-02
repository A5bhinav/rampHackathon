"""An unreliable Ramp (spec 0008). Releases that fail, are ignored, or can't restore the dates
always leave the hold on; under seeded chaos the money path keeps its invariants."""
from __future__ import annotations

import psycopg
import pytest
from dialback_core.db import system_tx
from dialback_core.detection import import_all, sync_vendor
from dialback_core.jobs import HOLD_EXPECTED
from dialback_core.ramp.port import RampError
from flows import case_of, change_and_detect, one, pay, release, report, transit

pytestmark = pytest.mark.anyio
UNFINISHED = ("open", "held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked", "releasing")

# Cases whose hold Dialback confirmed and hasn't released, in a state where the hold must be on,
# whose vendor has no hold in Ramp. Read from the mock's tables, because Ramp's API is chaotic.
MUST_BE_HELD = """
select c.id, v.ramp_vendor_id,
       exists (select 1 from mock_ramp.vendor_holds mh where mh.vendor_id = v.ramp_vendor_id::uuid and mh.released_at is null) as held
  from app.cases c
  join app.vendors v on v.id = c.vendor_id
  join app.holds h on h.case_id = c.id and h.released_at is null and h.status in ('confirmed', 'releasing')
 where c.dataset_id = %s and c.state::text = any(%s)
"""


async def verified(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    assert (await report(world, case["id"], world.voice.placed[-1]["call_id"])).status_code == 204
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert case["state"] == "awaiting_release"
    return case


async def held(world, ref="acme") -> bool:
    return (await world.ramp.get_vendor(world.refs[ref])).hold_id is not None


async def stuck_alert(world, case_id):
    return await one(world.pool, "select severity, message from app.alerts where case_id = %s and kind = 'command_stuck'", case_id)


async def test_a_release_that_fails_once_is_retried(world):
    case = await verified(world)
    await world.mock_settings(fail_next_release=True)
    await release(world, case["id"], case["version"])
    await world.worker.drain(world.dataset.id)
    assert (await case_of(world))["state"] == "released" and not await held(world)


@pytest.mark.parametrize("switch", ["fail_releases", "ignore_releases"], ids=["endpoint-down", "ignored"])
async def test_a_release_that_cant_be_done_or_isnt_done_leaves_the_hold_on(world, switch):
    case = await verified(world)
    await world.mock_settings(**{switch: True})
    await release(world, case["id"], case["version"])
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert case["state"] == "needs_review", "the release never confirmed, so a person takes over"
    assert await held(world), "the hold is still on in Ramp"
    alert = await stuck_alert(world, case["id"])
    assert alert["severity"] == "critical" and "release_hold" in alert["message"]
    assert await pay(world, "2026-12-31") == [], "R1: nothing is paid"


async def test_when_dates_cant_be_restored_the_hold_goes_back_on(world):
    case = await verified(world)
    await world.mock_settings(fail_bill_updates=True)
    await release(world, case["id"], case["version"])
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert case["state"] == "needs_review" and await held(world), "the hold went back on after each try"
    placed = await world.mock.get("/developer/v1/audit-logs/events", headers=world.auth,
                                  params={"target_id": world.refs["acme"], "event_type": "VENDOR_HOLD_PLACED"})
    tries = (await one(world.pool, "select attempts from app.outbox where case_id = %s and command = 'release_hold'", case["id"]))["attempts"]
    assert len(placed.json()["data"]) == 1 + tries, "placed once, then put back after every try that lifted it"
    alert = await stuck_alert(world, case["id"])
    assert alert["severity"] == "critical" and "release_hold" in alert["message"]
    assert await pay(world, "2026-12-31") == []


@pytest.mark.parametrize("step", ["release", "read_back", "confirm"], ids=["answer-lost", "read-back-errors", "database-error"])
async def test_a_release_that_fails_partway_puts_the_hold_back_before_retrying(world, step):
    case = await verified(world)
    ramp, worker = world.ramp, world.worker
    real_release = ramp.release_vendor_hold

    async def answer_lost(vendor_id: str) -> None:
        await real_release(vendor_id)  # Ramp lifted the hold, and the answer never arrived
        raise RampError("connection reset", retryable=True)

    async def read_back_errors(vendor_id: str):
        raise RampError("read timed out", retryable=True)

    async def database_error(*_):
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    target, name, fake = {"release": (ramp, "release_vendor_hold", answer_lost),
                          "read_back": (ramp, "get_vendor", read_back_errors),
                          "confirm": (worker, "_emit", database_error)}[step]
    await release(world, case["id"], case["version"])
    setattr(target, name, fake)
    try:
        await worker.run_commands(world.dataset.id, limit=1)
    finally:
        delattr(target, name)
    assert (await case_of(world))["state"] == "releasing"
    assert await held(world), "the hold went back on before the retry"
    await worker.drain(world.dataset.id)
    assert (await case_of(world))["state"] == "released" and not await held(world), "the retry released it"


async def test_a_release_that_races_a_change_alerts_even_when_ramp_wont_take_the_hold_back(world):
    case = await verified(world)
    await release(world, case["id"], case["version"])
    await world.mock_settings(silent_changes=True)
    await world.bank_change(account_number="000099990000")
    ramp = world.ramp
    real_release = ramp.release_vendor_hold

    async def release_then_detect_then_fail(vendor_id: str) -> None:
        await real_release(vendor_id)
        await sync_vendor(world.pool, ramp, world.keys, world.dataset.id, vendor_id, "poll")
        await world.mock_settings(fail_holds=True)

    ramp.release_vendor_hold = release_then_detect_then_fail
    try:
        await world.worker.run_commands(world.dataset.id, limit=1)
    finally:
        del ramp.release_vendor_hold
    alert = await one(world.pool, "select severity, message from app.alerts where case_id = %s and kind = 'vendor_not_held'", case["id"])
    assert alert["severity"] == "critical" and "could not be put back" in alert["message"]
    row = await one(world.pool, "select status, last_error from app.outbox where case_id = %s and command = 'release_hold'", case["id"])
    assert row["status"] == "failed" and row["last_error"].startswith("stale:"), "dropped as stale, not retried as a release"

    await world.mock_settings(fail_holds=False)  # Ramp recovers: the new version's hold goes on, and it calls again
    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    assert case["version"] == 2 and case["state"] in HOLD_EXPECTED and await held(world)


# Three runs at 30% errors, and three at 70% whose releases give up after every try, which are
# the runs that left a confirmed hold missing before releases put the hold back on any failure.
@pytest.mark.parametrize(("seed", "error_permille"), [(11, 300), (23, 300), (47, 300), (9, 700), (11, 700), (12, 700)])
async def test_the_money_path_holds_its_invariants_under_chaos(world, seed, error_permille):
    extra = [{"ref": f"v{i}", "name": f"Chaos Vendor {i} LLC",
              "accounts": [{"routing_number": "021000021", "account_number": f"00009000{i:04d}"}],
              "bills": [{"ref": f"v{i}-bill", "invoice_number": f"CH-{i}", "amount_cents": 1000, "payment_date": "2026-11-05"}]}
             for i in range(3)]
    r = await world.mock.post("/_mock/seed", headers=world.auth, json={"business_name": "Fictional Co", "vendors": extra})
    world.refs |= r.json()["refs"]
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    refs = ["acme", "v0", "v1", "v2"]
    last4 = {}
    refused: set = set()  # cases where Ramp refused to take a hold back; the reconcile job covers those
    real_put_back = world.worker._put_hold_back

    async def recording_put_back(ramp, vendor, case_id):
        if not (back := await real_put_back(ramp, vendor, case_id)):
            refused.add(case_id)
        return back

    world.worker._put_hold_back = recording_put_back
    # Retries under heavy chaos would trip the mock's rate limit, which isn't what this tests.
    await world.mock_settings(chaos_seed=seed, chaos_error_permille=error_permille, chaos_drop_permille=300,
                              rate_limit_per_10s=10_000)
    for i, ref in enumerate(refs):
        account = f"0000777{i:05d}"
        last4[ref] = account[-4:]
        await world.bank_change(ref, account_number=account)
    reported: set = set()
    paid_under_chaos: list = []
    for _ in range(6):
        await world.deliver_webhooks()
        await world.worker.jobs.run(world.dataset.id, "poll", force=True)  # dropped webhooks: the poller finds them
        await world.worker.drain(world.dataset.id)
        await _report_new_calls(world, reported, last4)
        await world.worker.drain(world.dataset.id)
        await _release_what_awaits(world)
        await world.worker.drain(world.dataset.id)
        # While Ramp is still failing, before any reconciliation: a confirmed hold stays on until
        # its case is released (I2), so a payment run pays nobody still being verified (R1).
        async with system_tx(world.pool) as conn:
            rows = [r for r in await (await conn.execute(MUST_BE_HELD, (world.dataset.id, list(HOLD_EXPECTED)))).fetchall()
                    if r["id"] not in refused]
        assert [r for r in rows if not r["held"]] == [], "I2 under chaos: a confirmed hold went missing"
        protected = {r["ramp_vendor_id"] for r in rows}
        paid = await pay(world, "2026-12-31")
        assert not [b for b in paid if await _vendor_of_bill(world, b) in protected], "R1 under chaos"
        paid_under_chaos += paid

    faults = world.mock._transport.app.state.chaos.injected
    async with system_tx(world.pool) as conn:
        dropped = (await (await conn.execute("select count(*) as n from mock_ramp.webhook_deliveries where status = 'dropped'")).fetchone())["n"]
        releases_tried = (await (await conn.execute(
            "select count(*) as n from app.outbox where dataset_id = %s and command = 'release_hold' and attempts > 0",
            (world.dataset.id,))).fetchone())["n"]
    assert faults > 0, "chaos injected Ramp failures"
    assert releases_tried > 0, "the release path ran under chaos"
    await world.mock_settings(chaos_seed=None)  # Ramp recovers; the jobs catch up and the rest finish
    for _ in range(3):
        for job in ("poll", "reconcile"):
            await world.worker.jobs.run(world.dataset.id, job, force=True)
        await world.worker.drain(world.dataset.id)
        await _report_new_calls(world, reported, last4)
        await world.worker.drain(world.dataset.id)
        await _release_what_awaits(world)
        await world.worker.drain(world.dataset.id)

    async with system_tx(world.pool) as conn:
        cases = await (await conn.execute(
            "select c.id, c.state::text as state, v.ramp_vendor_id from app.cases c join app.vendors v on v.id = c.vendor_id "
            "where c.dataset_id = %s", (world.dataset.id,))).fetchall()
        approved = {r["case_id"] for r in await (await conn.execute(
            "select case_id from audit.evidence_events where chain_id = %s and type = 'release_approved'",
            (world.dataset.chain_id,))).fetchall()}
        chain = await (await conn.execute("select ok from audit.verify_chain(%s)", (world.dataset.chain_id,))).fetchone()
    assert len(cases) == len(refs), "every change became exactly one case"
    unfinished = {c["ramp_vendor_id"] for c in cases if c["state"] in UNFINISHED}
    for c in cases:
        if c["state"] in UNFINISHED:  # I2: after reconciliation, every unfinished case's vendor is held
            assert (await world.ramp.get_vendor(c["ramp_vendor_id"])).hold_id is not None, c
        if c["state"] == "released":
            assert c["id"] in approved, "never released without an approver's release"
    paid = await pay(world, "2026-12-31")
    bills = {b.id: b for ref in refs for b in await world.ramp.list_bills_for_vendor(world.refs[ref])}
    assert not [b for b in paid if bills[b].vendor_id in unfinished], "R1: no bill of an unfinished case's vendor is paid"
    assert chain["ok"], "the evidence chain is intact"
    assert any(c["state"] == "released" for c in cases), "the money path still finishes once Ramp recovers"
    print(f"seed {seed} at {error_permille}‰: {faults} Ramp faults, {dropped} webhooks dropped, {releases_tried} releases tried under chaos, "
          f"{len(paid_under_chaos)} bills paid under chaos, {len(refused)} holds Ramp wouldn't take back, "
          f"states {sorted(c['state'] for c in cases)}")


async def _vendor_of_bill(world, bill_id: str) -> str:
    return str((await one(world.pool, "select vendor_id from mock_ramp.bills where id = %s", bill_id))["vendor_id"])


async def _report_new_calls(world, reported: set, last4: dict) -> None:
    async with system_tx(world.pool) as conn:
        names = {r["id"]: r["ramp_vendor_id"] for r in await (await conn.execute(
            "select c.id, v.ramp_vendor_id from app.cases c join app.vendors v on v.id = c.vendor_id where c.dataset_id = %s",
            (world.dataset.id,))).fetchall()}
    by_vendor = {world.refs[ref]: ref for ref in last4}
    for placed in world.voice.placed:
        if placed["call_id"] in reported:
            continue
        reported.add(placed["call_id"])
        ref = by_vendor[names[placed["case_id"]]]
        await report(world, placed["case_id"], placed["call_id"], attempt=placed["attempt"],
                     answers=transit(placed["call_id"], digits=last4[ref],
                                     company="Acme Fasteners" if ref == "acme" else f"Chaos Vendor {ref[1:]}"))


async def _release_what_awaits(world) -> None:
    async with system_tx(world.pool) as conn:
        waiting = await (await conn.execute(
            "select id, version from app.cases where dataset_id = %s and state = 'awaiting_release'", (world.dataset.id,))).fetchall()
    for case in waiting:
        await release(world, case["id"], case["version"])
