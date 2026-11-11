"""Spec 0100: the sweep, the first import and the poll read each vendor's accounts once, using the
vendor Ramp's list already gave, five vendors at a time; one vendor's error doesn't stop the others.

`CountingRamp` is a Ramp port that counts what it is asked: the vendor reads, the account reads, and
how many account reads are in flight at once (each takes a moment, as on the sandbox).
"""
from __future__ import annotations

import asyncio
import dataclasses
import uuid
from types import SimpleNamespace

import pytest
from conftest import FINGERPRINT_KEY, core_settings
from dialback_core.datasets import create_dataset
from dialback_core.detection import Keys, import_all
from dialback_core.jobs import Jobs
from dialback_core.ramp.port import RampAccount, RampError, RampVendor

pytestmark = pytest.mark.anyio


class CountingRamp:
    def __init__(self, vendors: int = 12) -> None:
        self.vendors = [RampVendor(id=f"v-{i:02d}", name=f"Vendor {i:02d}", status="ACTIVE", updated_at=None, hold_id=None)
                        for i in range(vendors)]
        self.fail: set[str] = set()
        self.vendor_reads = 0
        self.account_reads: list[str] = []
        self.in_flight = self.most_in_flight = 0

    async def list_vendors_updated_since(self, since):
        return list(self.vendors)

    async def get_vendor(self, vendor_id: str) -> RampVendor:
        self.vendor_reads += 1
        return next(v for v in self.vendors if v.id == vendor_id)

    async def list_vendor_accounts(self, vendor_id: str) -> list[RampAccount]:
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)  # a read takes a moment
            if vendor_id in self.fail:
                raise RampError(f"accounts of {vendor_id}: HTTP 500", status=500, retryable=True)
            self.account_reads.append(vendor_id)
            return [RampAccount(id=f"a-{vendor_id}", vendor_id=vendor_id, routing_number="", last4="1234",
                                account_type=None, bank_name=None, is_active=True, created_at=None)]
        finally:
            self.in_flight -= 1

    async def get_audit_events(self, target_id: str, event_type: str | None = None):
        return []

    def reset(self) -> None:
        self.vendor_reads, self.account_reads, self.most_in_flight = 0, [], 0


@pytest.fixture
async def setup(pool):
    ramp = CountingRamp()
    dataset = await create_dataset(pool, "dev", f"sweep-{uuid.uuid4().hex[:8]}")
    keys = Keys(FINGERPRINT_KEY)
    jobs = Jobs(pool, core_settings(), SimpleNamespace(for_dataset=lambda _: ramp), keys)
    return SimpleNamespace(ramp=ramp, pool=pool, dataset=dataset.id, keys=keys, jobs=jobs)


# D4: the scheduled jobs run beside the case steps ----------------------------------------------------

def looping(world, run_jobs: bool = True) -> SimpleNamespace:
    """The real worker loop, with its jobs and commands replaced by ones the test can watch and block."""
    worker = world.worker
    worker.settings = dataclasses.replace(worker.settings, run_jobs=run_jobs, worker_poll_seconds=0.01)
    seen = SimpleNamespace(worker=worker, stop=asyncio.Event(), job_started=asyncio.Event(), job_may_end=asyncio.Event(),
                           commands_ran=asyncio.Event(), job_calls=0, fail_first_job=False)

    async def run_due(dataset_id=None) -> int:
        seen.job_calls += 1
        if seen.fail_first_job and seen.job_calls == 1:
            raise RuntimeError("a job went wrong")
        seen.job_started.set()
        await seen.job_may_end.wait()
        return 0

    async def run_commands(dataset_id=None) -> int:
        seen.commands_ran.set()
        return 0

    worker.jobs.run_due, worker.run_commands = run_due, run_commands
    return seen


async def stop_loop(seen, task) -> None:
    seen.stop.set()
    seen.job_may_end.set()
    await asyncio.wait_for(task, 5)


async def test_case_steps_run_while_a_job_is_running(world):
    seen = looping(world)
    task = asyncio.create_task(seen.worker.run_forever(seen.stop))
    try:
        await asyncio.wait_for(seen.job_started.wait(), 5)
        await asyncio.wait_for(seen.commands_ran.wait(), 5)  # the job hasn't ended
    finally:
        await stop_loop(seen, task)


async def test_a_failing_job_doesnt_stop_later_jobs(world):
    seen = looping(world)
    seen.fail_first_job = True
    task = asyncio.create_task(seen.worker.run_forever(seen.stop))
    try:
        await asyncio.wait_for(seen.job_started.wait(), 5)  # reached only on the second call
        assert seen.job_calls == 2
    finally:
        await stop_loop(seen, task)


async def test_with_run_jobs_off_no_job_runs(world):
    seen = looping(world, run_jobs=False)
    task = asyncio.create_task(seen.worker.run_forever(seen.stop))
    try:
        await asyncio.wait_for(seen.commands_ran.wait(), 5)
        await asyncio.sleep(0.05)
        assert seen.job_calls == 0
    finally:
        await stop_loop(seen, task)


async def test_shutdown_waits_for_a_running_job(world):
    seen = looping(world)
    task = asyncio.create_task(seen.worker.run_forever(seen.stop))
    await asyncio.wait_for(seen.job_started.wait(), 5)
    seen.stop.set()
    await asyncio.sleep(0.05)
    assert not task.done(), "the job is still running"
    await stop_loop(seen, task)


async def test_a_tick_still_runs_the_jobs_unless_told_not_to(world):
    seen = looping(world)
    seen.job_may_end.set()
    await seen.worker.tick()
    assert seen.job_calls == 1
    await seen.worker.tick(jobs=False)
    assert seen.job_calls == 1


# D1 to D3: reading each vendor once, five at a time ----------------------------------------------------

async def test_the_sweep_the_first_import_and_the_poll_read_each_vendors_accounts_once_and_the_vendor_never(setup):
    ids = [v.id for v in setup.ramp.vendors]
    assert await import_all(setup.pool, setup.ramp, setup.keys, setup.dataset) == 12
    assert setup.ramp.vendor_reads == 0 and sorted(setup.ramp.account_reads) == ids
    for run in (lambda: setup.jobs.full_sweep(setup.dataset, {}),
                lambda: setup.jobs.poll(setup.dataset, {"watermark": None})):
        setup.ramp.reset()
        await run()
        assert setup.ramp.vendor_reads == 0 and sorted(setup.ramp.account_reads) == ids


async def test_a_sweep_has_five_vendors_in_flight_and_never_more(setup):
    await setup.jobs.full_sweep(setup.dataset, {})
    assert setup.ramp.most_in_flight == 5


async def test_a_vendors_error_leaves_the_others_synced_and_the_sweep_still_fails_with_the_first(setup):
    await import_all(setup.pool, setup.ramp, setup.keys, setup.dataset)
    setup.ramp.reset()
    setup.ramp.fail = {"v-07", "v-03"}
    with pytest.raises(RampError, match="v-03"):  # the first in the list's order, as a plain RampError
        await setup.jobs.full_sweep(setup.dataset, {})
    assert sorted(setup.ramp.account_reads) == [v.id for v in setup.ramp.vendors if v.id not in {"v-03", "v-07"}]
