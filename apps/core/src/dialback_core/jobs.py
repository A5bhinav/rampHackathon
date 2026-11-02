"""Scheduled jobs: the poller, the nightly sweep and reconciliation (spec 0003).

- `poll`: vendors Ramp says were updated since the watermark, synced through the same
  `sync_vendor` the webhook path uses, so every door converges on one change and one case.
  A dataset's first poll is a full import: the baseline.
- `full_sweep`: every vendor, once a day, for a change Ramp didn't reflect in `updated_at`.
- `reconcile`: every vendor with an unfinished case must be held in Ramp (invariant I2). A
  missing hold is put back at once and raised as a critical alert (R1, R7).
- `verify_chain`: recompute the dataset's evidence chain; a broken row raises one critical
  alert naming it (spec 0004).

Jobs are rows in `app.job_runs`, due on the dataset's clock and claimed with a lease, so two
workers never run the same job and the lab drives them by moving a scenario's clock.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import secrets
import socket
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

JOBS = ("poll", "full_sweep", "reconcile", "verify_chain")
WATERMARK_OVERLAP = dt.timedelta(minutes=2)
# Where the hold should be on. `open` (being placed) and `releasing` (being lifted on
# purpose) are left alone.
HOLD_EXPECTED = ("held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked")
REHOLD_REASON = "Dialback: the hold was missing while the bank change is still being verified."


def unique_worker_name() -> str:
    """A name only this process uses, so a lease (job or command) says which worker holds it."""
    return f"core-worker:{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"


@dataclass
class JobResult:
    effects: int = 0                      # changes found or holds put back: work the next tick should see
    result: dict[str, Any] = field(default_factory=dict)
    watermark: dt.datetime | None = None


class Jobs:
    def __init__(self, pool: AsyncConnectionPool, settings: Settings, connector: RampConnector, keys: Keys,
                 worker_name: str | None = None) -> None:
        self.pool, self.connector, self.keys = pool, connector, keys
        self.name = worker_name or unique_worker_name()
        self.intervals = {
            "poll": dt.timedelta(seconds=settings.poll_interval_seconds),
            "full_sweep": dt.timedelta(seconds=settings.full_sweep_interval_seconds),
            "reconcile": dt.timedelta(seconds=settings.reconcile_interval_seconds),
            "verify_chain": dt.timedelta(seconds=settings.verify_interval_seconds),
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
        if not ok:  # a failed run keeps the last good result: the chain check's tip lives there
            outcome = JobResult(result=claimed["last_result"])
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

    # The evidence-chain check (spec 0004) ---------------------------------------------------

    async def verify_chain(self, dataset_id: UUID, claimed: dict[str, Any]) -> JobResult:
        """Recompute the chain with the same SQL as the dashboard badge, then check that the tip
        the last run saw is still there, unchanged. A broken chain is a finding, not a job
        failure: one critical alert per dataset, never resolved here."""
        async with system_tx(self.pool) as conn:
            chain_id = (await (await conn.execute("select chain_id from app.datasets where id = %s",
                                                  (dataset_id,))).fetchone())["chain_id"]
            v = await (await conn.execute(
                "select v.ok, v.checked, v.first_bad_seq, v.detail, t.seq as tip_seq, t.hash as tip_hash "
                "from audit.verify_chain(%s) v left join audit.chain_tip(%s) t on true", (chain_id, chain_id))).fetchone()
            ok, bad_seq, detail = v["ok"], v["first_bad_seq"], v["detail"]
            # The bad row is the one verify_chain stopped at, unless that row comes after a missing one.
            own_row = bad_seq == v["checked"] + 1
            seen_seq, seen_hash = claimed["last_result"].get("tip_seq"), claimed["last_result"].get("tip_hash")
            if ok and seen_seq is not None:
                # The chain only grows, so a recomputed or shortened chain still fails here (spec 0004, D5).
                seen = await (await conn.execute("select hash from audit.evidence_events where chain_id = %s and seq = %s",
                                                 (chain_id, seen_seq))).fetchone()
                if seen is None or seen["hash"] != seen_hash:
                    ok, bad_seq, own_row = False, seen_seq, seen is not None
                    detail = ("the tip the last check saw is gone (rows deleted)" if seen is None
                              else "the row at the last check's tip was rewritten")
            if not ok:
                # Link the case only if the bad row's case_id names a real case: the row itself can't be trusted.
                case = await (await conn.execute(
                    "select c.id from audit.evidence_events e join app.cases c on c.id = e.case_id "
                    "where e.chain_id = %s and e.seq = %s and %s", (chain_id, bad_seq, own_row))).fetchone()
                await conn.execute(
                    """insert into app.alerts (dataset_id, case_id, kind, severity, message, raised_at)
                       values (%s, %s, 'chain_broken', 'critical', %s, app.now(%s))
                       on conflict (dataset_id) where kind = 'chain_broken' and resolved_at is null do nothing""",
                    (dataset_id, case["id"] if case else None,
                     f"The evidence chain is broken at event #{bad_seq}: {detail}.", dataset_id))
                log.error("evidence chain for dataset %s is broken at event %s: %s", dataset_id, bad_seq, detail)
        return JobResult(result={"ok": ok, "checked": v["checked"], "first_bad_seq": bad_seq,
                                 "detail": detail, "tip_seq": v["tip_seq"], "tip_hash": v["tip_hash"]})
