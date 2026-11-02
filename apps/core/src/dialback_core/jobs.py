"""Scheduled jobs: the poller, the nightly sweep and reconciliation (spec 0003).

- `poll`: vendors Ramp says were updated since the watermark, synced through the same
  `sync_vendor` the webhook path uses, so every door converges on one change and one case.
  A dataset's first poll is a full import: the baseline.
- `full_sweep`: every vendor, once a day, for a change Ramp didn't reflect in `updated_at`.
- `reconcile`: every vendor with an unfinished case must be held in Ramp (invariant I2). A
  missing hold is put back at once and raised as a critical alert (R1, R7).

Jobs are rows in `app.job_runs`, due on the dataset's clock and claimed with a lease, so two
workers never run the same job and the lab drives them by moving a scenario's clock.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .db import dataset_now, system_tx
from .detection import Keys, sync_vendor
from .ramp import RampConnector, RampError, RampPort
from .settings import Settings

log = logging.getLogger(__name__)

JOBS = ("poll", "full_sweep", "reconcile")
WATERMARK_OVERLAP = dt.timedelta(minutes=2)
# Where the hold should be on. `open` (being placed) and `releasing` (being lifted on
# purpose) are left alone.
HOLD_EXPECTED = ("held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked")
REHOLD_REASON = "Dialback: the hold was missing while the bank change is still being verified."


@dataclass
class JobResult:
    effects: int = 0                      # changes found or holds put back: work the next tick should see
    result: dict[str, Any] = field(default_factory=dict)
    watermark: dt.datetime | None = None


class Jobs:
    def __init__(self, pool: AsyncConnectionPool, settings: Settings, connector: RampConnector, keys: Keys,
                 worker_name: str = "core-worker") -> None:
        self.pool, self.connector, self.keys, self.name = pool, connector, keys, worker_name
        self.intervals = {
            "poll": dt.timedelta(seconds=settings.poll_interval_seconds),
            "full_sweep": dt.timedelta(seconds=settings.full_sweep_interval_seconds),
            "reconcile": dt.timedelta(seconds=settings.reconcile_interval_seconds),
        }
        self.lease = dt.timedelta(seconds=settings.job_lease_seconds)

    async def run_due(self, dataset_id: UUID | None = None) -> int:
        """Run every due job for every live dataset (or one). Returns the effects found."""
        async with system_tx(self.pool) as conn:
            rows = await (await conn.execute(
                "select id from app.datasets where retired_at is null and (%s::uuid is null or id = %s::uuid) order by created_at",
                (dataset_id, dataset_id))).fetchall()
        effects = 0
        for row in rows:
            for job in JOBS:
                effects += await self.run(row["id"], job)
        return effects

    async def run(self, dataset_id: UUID, job: str, force: bool = False) -> int:
        """Claim and run one job. `force` ignores the interval (the lease still applies)."""
        interval = dt.timedelta(0) if force else self.intervals[job]
        async with system_tx(self.pool) as conn:
            claimed = await (await conn.execute("select * from app.claim_job(%s, %s, %s, %s, %s)",
                                                (dataset_id, job, interval, self.name, self.lease))).fetchone()
        if claimed is None:
            return 0
        ok, error, outcome = True, None, JobResult()
        try:
            outcome = await getattr(self, job)(dataset_id, claimed)
        except RampError as exc:
            ok, error = False, str(exc)
            log.warning("job %s for dataset %s failed: %s", job, dataset_id, exc)
        except Exception as exc:
            ok, error = False, type(exc).__name__
            log.exception("job %s for dataset %s failed", job, dataset_id)
        async with system_tx(self.pool) as conn:
            await conn.execute("select app.finish_job(%s, %s, %s, %s, %s, %s, %s)",
                               (dataset_id, job, self.name, ok, error, Jsonb(outcome.result), outcome.watermark))
        return outcome.effects

    # The poller -------------------------------------------------------------------------------

    async def _sync(self, ramp: RampPort, dataset_id: UUID, vendor_ids: list[str]) -> int:
        found = 0
        for vendor_id in vendor_ids:
            changes = await sync_vendor(self.pool, ramp, self.keys, dataset_id, vendor_id, "poll")
            found += sum(1 for c in changes if c.status == "applied")
        return found

    async def poll(self, dataset_id: UUID, claimed: dict[str, Any]) -> JobResult:
        ramp = self.connector.for_dataset(dataset_id)
        started = dt.datetime.now(dt.UTC)  # Ramp's clock, not the dataset's: the watermark is compared with Ramp's updated_at
        since = claimed["watermark"]
        vendors = await ramp.list_vendors_updated_since(since)
        found = await self._sync(ramp, dataset_id, [v.id for v in vendors])
        mode = "full" if since is None else "incremental"
        return JobResult(effects=found, watermark=started - WATERMARK_OVERLAP,
                         result={"mode": mode, "vendors_checked": len(vendors), "changes_found": found})

    async def full_sweep(self, dataset_id: UUID, claimed: dict[str, Any]) -> JobResult:
        ramp = self.connector.for_dataset(dataset_id)
        vendors = await ramp.list_vendors_updated_since(None)
        found = await self._sync(ramp, dataset_id, [v.id for v in vendors])
        return JobResult(effects=found, result={"vendors_checked": len(vendors), "changes_found": found})

    # Reconciliation (invariant I2) ----------------------------------------------------------

    async def reconcile(self, dataset_id: UUID, claimed: dict[str, Any]) -> JobResult:
        ramp = self.connector.for_dataset(dataset_id)
        async with system_tx(self.pool) as conn:
            cases = await (await conn.execute(
                """select c.id, v.ramp_vendor_id from app.cases c join app.vendors v on v.id = c.vendor_id
                    where c.dataset_id = %s and c.state::text = any(%s) order by c.opened_at""",
                (dataset_id, list(HOLD_EXPECTED)))).fetchall()
        reheld = failed = 0
        for case in cases:
            if (await ramp.get_vendor(case["ramp_vendor_id"])).hold_id is not None:
                continue
            put_back = await self._put_hold_back(ramp, dataset_id, case["id"], case["ramp_vendor_id"])
            if put_back is True:
                reheld += 1
            elif put_back is False:
                failed += 1
        return JobResult(effects=reheld, result={"cases_checked": len(cases), "holds_put_back": reheld,
                                                 "holds_still_missing": failed})

    async def _put_hold_back(self, ramp: RampPort, dataset_id: UUID, case_id: UUID, vendor_id: str) -> bool | None:
        """Re-place a missing hold. Returns True if it is back on, False if Ramp refused, None if
        the case moved on meanwhile.

        The case row stays locked while Ramp is called, so a release can't interleave: either
        the release committed first (the case is `releasing`, and we leave it alone) or it waits
        until the hold is back and then lifts it on purpose.
        """
        async with system_tx(self.pool) as conn:
            row = await (await conn.execute("select app.lock_case(%s) as state", (case_id,))).fetchone()
            if row["state"] is None or row["state"] not in HOLD_EXPECTED:
                return None
            now = await dataset_now(conn, dataset_id)
            try:
                hold = await ramp.place_vendor_hold(vendor_id, REHOLD_REASON)
                back = (await ramp.get_vendor(vendor_id)).hold_id is not None
            except RampError as exc:
                log.warning("reconcile: could not put the hold back for case %s: %s", case_id, exc)
                back, hold = False, None
            if back:
                updated = await conn.execute(
                    "update app.holds set status = 'confirmed', ramp_ref = %s, confirmed_at = %s, last_error = null "
                    "where case_id = %s and released_at is null", (hold.id if hold else None, now, case_id))
                if updated.rowcount == 0:
                    await conn.execute(
                        "insert into app.holds (dataset_id, case_id, vendor_id, ramp_ref, status, placed_at, confirmed_at) "
                        "select dataset_id, id, vendor_id, %s, 'confirmed', %s, %s from app.cases where id = %s",
                        (hold.id if hold else None, now, now, case_id))
            message = ("The hold on this vendor was missing in Ramp; Dialback put it back."
                       if back else "The hold on this vendor is missing in Ramp and could not be put back; payments may go out.")
            await conn.execute(
                """insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at)
                   select %s, %s, 'vendor_not_held', 'critical', %s, %s
                    where not exists (select 1 from app.alerts a where a.case_id = %s and a.kind = 'vendor_not_held'
                                        and a.resolved_at is null)""",
                (dataset_id, case_id, message, now, case_id))
        log.warning("reconcile: case %s vendor %s was not held in Ramp; put back: %s", case_id, vendor_id, back)
        return back
