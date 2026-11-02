"""An unreliable Ramp (spec 0008). Releases that fail, are ignored, or can't restore the dates
always leave the hold on; under seeded chaos the money path keeps its invariants."""
from __future__ import annotations

import pytest
from dialback_core.db import system_tx
from dialback_core.detection import import_all
from flows import case_of, change_and_detect, one, pay, release, report, transit

pytestmark = pytest.mark.anyio
UNFINISHED = ("open", "held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked", "releasing")


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
    assert len(placed.json()["data"]) >= 2, "put back after the release that couldn't finish"
    assert await pay(world, "2026-12-31") == []


@pytest.mark.parametrize("seed", [11, 23, 47])
async def test_the_money_path_holds_its_invariants_under_chaos(world, seed):
    extra = [{"ref": f"v{i}", "name": f"Chaos Vendor {i} LLC",
              "accounts": [{"routing_number": "021000021", "account_number": f"00009000{i:04d}"}],
              "bills": [{"ref": f"v{i}-bill", "invoice_number": f"CH-{i}", "amount_cents": 1000, "payment_date": "2026-11-05"}]}
             for i in range(3)]
    r = await world.mock.post("/_mock/seed", headers=world.auth, json={"business_name": "Fictional Co", "vendors": extra})
    world.refs |= r.json()["refs"]
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    refs = ["acme", "v0", "v1", "v2"]
    last4 = {}

    await world.mock_settings(chaos_seed=seed, chaos_error_permille=300, chaos_drop_permille=300)
    for i, ref in enumerate(refs):
        account = f"0000777{i:05d}"
        last4[ref] = account[-4:]
        await world.bank_change(ref, account_number=account)
    reported: set = set()
    for _ in range(6):
        await world.deliver_webhooks()
        await world.worker.jobs.run(world.dataset.id, "poll", force=True)  # dropped webhooks: the poller finds them
        await world.worker.drain(world.dataset.id)
        await _report_new_calls(world, reported, last4)
        await world.worker.drain(world.dataset.id)
        await _release_what_awaits(world)
        await world.worker.drain(world.dataset.id)

    faults = world.mock._transport.app.state.chaos.injected
    async with system_tx(world.pool) as conn:
        dropped = (await (await conn.execute("select count(*) as n from mock_ramp.webhook_deliveries where status = 'dropped'")).fetchone())["n"]
    assert faults > 0, "chaos injected Ramp failures"
    await world.mock_settings(chaos_seed=None)  # Ramp recovers; the jobs catch up
    for _ in range(3):
        for job in ("poll", "reconcile"):
            await world.worker.jobs.run(world.dataset.id, job, force=True)
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
    print(f"seed {seed}: {faults} Ramp faults, {dropped} webhooks dropped, states {sorted(c['state'] for c in cases)}")


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
