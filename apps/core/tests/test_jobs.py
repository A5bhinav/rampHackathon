"""The poller and reconciliation (spec 0003): silent changes are found, missing holds go
back on, and jobs run once at a time on the dataset's clock."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from conftest import core_settings, make_world
from dialback_core.db import system_tx
from dialback_core.detection import import_all, process_inbound_webhooks
from flows import case_of, change_and_detect, one, release, report

pytestmark = pytest.mark.anyio


async def job_row(world, job: str):
    return await one(world.pool, "select * from app.job_runs where dataset_id = %s and job = %s", world.dataset.id, job)


async def lift_hold_by_hand(world) -> None:
    """Someone lifts the vendor hold directly in Ramp."""
    r = await world.mock.post(f"/developer/v1/vendors/{world.refs['acme']}/release", headers=world.auth)
    assert r.status_code == 200 and r.json()["released"] is True


async def held_in_ramp(world) -> bool:
    return (await world.ramp.get_vendor(world.refs["acme"])).hold_id is not None


async def alerts(world, kind: str) -> list[dict]:
    async with system_tx(world.pool) as conn:
        return await (await conn.execute("select * from app.alerts where dataset_id = %s and kind = %s",
                                         (world.dataset.id, kind))).fetchall()


async def test_a_job_runs_once_at_a_time(world):
    claim = "select * from app.claim_job(%s, 'reconcile', interval '0 seconds', %s, interval '10 minutes')"
    async with system_tx(world.pool) as conn:
        first = await (await conn.execute(claim, (world.dataset.id, "worker-a"))).fetchone()
    async with system_tx(world.pool) as conn:
        second = await (await conn.execute(claim, (world.dataset.id, "worker-b"))).fetchone()
        # Only the holder can finish (and so release) the lease.
        await conn.execute("select app.finish_job(%s, 'reconcile', 'worker-b', true, null, '{}', null)", (world.dataset.id,))
    assert first is not None and first["locked_by"] == "worker-a"
    assert second is None, "the lease keeps a second worker out"
    async with system_tx(world.pool) as conn:
        await conn.execute("select app.finish_job(%s, 'reconcile', 'worker-a', true, null, '{}', null)", (world.dataset.id,))
        third = await (await conn.execute(claim, (world.dataset.id, "worker-b"))).fetchone()
    assert third is not None and third["runs"] == 2


async def test_the_poller_baselines_then_finds_a_silent_change(world):
    jobs = world.worker.jobs
    assert await jobs.run(world.dataset.id, "poll") == 0
    first = await job_row(world, "poll")
    assert first["last_ok"] and first["last_result"]["mode"] == "full" and first["watermark"] is not None
    assert (await one(world.pool, "select count(*) as n from app.bank_accounts where dataset_id = %s", world.dataset.id))["n"] == 2
    assert await case_of(world) is None, "the first poll is a baseline, not a change"

    await world.mock_settings(silent_changes=True)
    await world.bank_change()
    assert await world.deliver_webhooks() == 0, "Ramp announced nothing"
    assert await jobs.run(world.dataset.id, "poll") == 0, "not due yet: the interval hasn't passed"

    ramp, real_list, asked = world.ramp, world.ramp.list_vendors_updated_since, []

    async def spy(since):
        asked.append(since)
        return await real_list(since)

    ramp.list_vendors_updated_since = spy
    try:
        assert await jobs.run(world.dataset.id, "poll", force=True) == 1
    finally:
        del ramp.list_vendors_updated_since
    assert asked == [first["watermark"]], "the incremental poll asks only for vendors updated since the watermark"
    case = await one(world.pool, """select c.state, ce.detected_via from app.cases c
                                     join app.bank_change_events ce on ce.id = c.change_event_id where c.dataset_id = %s""",
                     world.dataset.id)
    assert (case["state"], case["detected_via"]) == ("open", "poll")
    second = await job_row(world, "poll")
    assert second["last_result"] == {"mode": "incremental", "vendors_checked": 1, "changes_found": 1}
    assert second["watermark"] > first["watermark"]


async def test_reconciliation_puts_a_lifted_hold_back_once(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    assert (await case_of(world))["state"] == "calling" and await held_in_ramp(world)
    jobs = world.worker.jobs
    assert await jobs.run(world.dataset.id, "reconcile", force=True) == 0, "held: nothing to do"

    await lift_hold_by_hand(world)
    assert await jobs.run(world.dataset.id, "reconcile", force=True) == 1
    assert await held_in_ramp(world)
    [alert] = await alerts(world, "vendor_not_held")
    assert alert["severity"] == "critical" and "put it back" in alert["message"]
    assert (await job_row(world, "reconcile"))["last_result"] == {"cases_checked": 1, "holds_put_back": 1, "holds_still_missing": 0}

    await lift_hold_by_hand(world)
    assert await jobs.run(world.dataset.id, "reconcile", force=True) == 1
    assert await held_in_ramp(world) and len(await alerts(world, "vendor_not_held")) == 1, "one alert per case, not one per run"
    assert (await case_of(world))["state"] == "calling", "the case itself didn't move"


async def test_reconciliation_holds_once_ramp_recovers(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.mock_settings(fail_holds=True)
    await change_and_detect(world)
    case = await case_of(world)
    assert case["state"] == "needs_review" and not await held_in_ramp(world)
    jobs = world.worker.jobs

    assert await jobs.run(world.dataset.id, "reconcile", force=True) == 0
    assert (await job_row(world, "reconcile"))["last_result"]["holds_still_missing"] == 1
    [alert] = await alerts(world, "vendor_not_held")
    assert "could not be put back" in alert["message"]

    await world.mock_settings(fail_holds=False)
    assert await jobs.run(world.dataset.id, "reconcile", force=True) == 1
    assert await held_in_ramp(world)
    hold = await one(world.pool, "select status, ramp_ref from app.holds where case_id = %s and released_at is null", case["id"])
    assert hold["status"] == "confirmed" and hold["ramp_ref"]
    assert (await case_of(world))["state"] == "needs_review", "a person still has to review it"


async def test_reconciliation_leaves_open_and_releasing_cases_alone(world):
    jobs = world.worker.jobs
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.bank_change()
    await world.deliver_webhooks()
    await process_inbound_webhooks(world.pool, world.connector, world.keys, dataset_id=world.dataset.id)
    assert (await case_of(world))["state"] == "open" and not await held_in_ramp(world)
    assert await jobs.run(world.dataset.id, "reconcile", force=True) == 0, "open: the hold is being placed"

    await world.worker.drain(world.dataset.id)
    case = await case_of(world)
    await report(world, case["id"], world.voice.placed[-1]["call_id"])
    await world.worker.drain(world.dataset.id)
    await release(world, case["id"], 1)
    assert (await case_of(world))["state"] == "releasing"
    await lift_hold_by_hand(world)  # as the release command is about to
    assert await jobs.run(world.dataset.id, "reconcile", force=True) == 0, "releasing: the hold comes off on purpose"
    assert not await held_in_ramp(world)
    await world.worker.drain(world.dataset.id)
    assert (await case_of(world))["state"] == "released"


async def test_jobs_are_due_on_the_lab_clock(pool):
    start = dt.datetime(2026, 11, 3, 18, 0, tzinfo=dt.UTC)
    world = await make_world(pool, kind="lab", sim_now=start,
                             settings=core_settings(run_jobs=True, poll_interval_seconds=60, reconcile_interval_seconds=300))
    try:
        async def runs() -> dict[str, int]:
            async with system_tx(pool) as conn:
                rows = await (await conn.execute("select job, runs, last_started_at from app.job_runs where dataset_id = %s",
                                                 (world.dataset.id,))).fetchall()
            return {r["job"]: (r["runs"], r["last_started_at"]) for r in rows}

        async def advance(to: dt.datetime) -> None:
            async with system_tx(pool) as conn:
                await conn.execute("select app.advance_clock(%s, %s)", (world.dataset.id, to))
            await world.worker.drain(world.dataset.id)

        await world.worker.drain(world.dataset.id)
        assert {job: n for job, (n, _) in (await runs()).items()} == {"poll": 1, "full_sweep": 1, "reconcile": 1}
        assert (await runs())["poll"][1] == start, "started on the simulated clock"
        await advance(start + dt.timedelta(seconds=59))
        assert (await runs())["poll"][0] == 1, "not due before the interval"
        await advance(start + dt.timedelta(seconds=60))
        assert {job: n for job, (n, _) in (await runs()).items()} == {"poll": 2, "full_sweep": 1, "reconcile": 1}
        await advance(start + dt.timedelta(seconds=300))
        assert {job: n for job, (n, _) in (await runs()).items()} == {"poll": 3, "full_sweep": 1, "reconcile": 2}

        # A silent change on the lab dataset is found by moving the clock, as a scenario would.
        await world.mock_settings(silent_changes=True)
        await world.bank_change()
        await advance(start + dt.timedelta(seconds=360))
        case = await case_of(world)
        assert case is not None and case["state"] == "calling", "found by the poller, held, and called"
    finally:
        await world.core.aclose()
        await world.mock.aclose()


async def test_a_dataset_without_ramp_records_the_error_and_moves_on(world, pool):
    from dialback_core.datasets import create_dataset
    bare = await create_dataset(pool, "dev", f"no-ramp-{uuid.uuid4().hex[:6]}")
    assert await world.worker.jobs.run(bare.id, "poll") == 0
    row = await one(pool, "select last_ok, last_error, locked_by from app.job_runs where dataset_id = %s and job = 'poll'", bare.id)
    assert row["last_ok"] is False and "401" in row["last_error"] and row["locked_by"] is None


async def test_a_release_waits_behind_a_reconciliation_lock(world):
    """The lock that serializes a re-hold against a release (spec 0003, decision 3)."""
    from dialback_core.db import person_tx
    from psycopg import errors

    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await change_and_detect(world)
    case = await case_of(world)
    await report(world, case["id"], world.voice.placed[-1]["call_id"])
    await world.worker.drain(world.dataset.id)
    assert (await case_of(world))["state"] == "awaiting_release"

    async with system_tx(world.pool) as reconciling:
        assert (await (await reconciling.execute("select app.lock_case(%s) as s", (case["id"],))).fetchone())["s"] == "awaiting_release"
        with pytest.raises(errors.LockNotAvailable):
            async with person_tx(world.pool, world.users["approver1"], {}) as releasing:
                await releasing.execute("set local lock_timeout = '300ms'")
                await releasing.execute("select app.release_case(%s, 1, 'approve', 'Confirmed.')", (case["id"],))
    # Lock gone: the same release goes through.
    assert (await release(world, case["id"], 1))["to_state"] == "releasing"

    with pytest.raises(errors.InsufficientPrivilege):
        async with person_tx(world.pool, world.users["approver1"], {}) as person:
            await person.execute("select app.lock_case(%s)", (case["id"],))
