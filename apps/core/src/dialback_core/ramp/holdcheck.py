"""Hold and release one vendor in Ramp's sandbox the way the worker does, and leave it as found (spec 0099).

    export RAMP_SANDBOX_CLIENT_ID='...'
    read -rs RAMP_SANDBOX_CLIENT_SECRET && export RAMP_SANDBOX_CLIENT_SECRET
    uv run --package dialback-core python -m dialback_core.ramp.holdcheck --vendor <ramp vendor id> --yes

Through the real layer (spec 0097): save each unpaid bill's payment date, hold the vendor, read the
hold back, release it, read that back, and set each saved date again. It writes only to that vendor
and its bills, refuses a vendor already on hold, and cleans up even when a read-back fails. No secret
or token is ever printed. It never opens a case or touches Dialback's database.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable

import httpx

from ..settings import ConfigurationError, RampSandbox, _ramp_sandbox
from .http import TokenBucket
from .login import RampLogin
from .port import RampError
from .sandbox import RampSandboxAdapter

NEEDED = ("vendors:read", "vendors:write", "bills:read", "bills:write", "audit_logs:read")
REASON = "Dialback sandbox hold check: a test hold, released within a minute."
WORKER_READS = 3  # worker.py's _read_back_hold: three reads about 0.6 seconds apart in all, then the step retries
BY_HAND = "Vendors, open the vendor, Status, Active, Remove hold"
HOW_TO = f"""No Ramp sandbox credentials in this terminal. Set them first (the secret isn't shown or saved):

    export RAMP_SANDBOX_CLIENT_ID='your sandbox app's client id'
    read -rs RAMP_SANDBOX_CLIENT_SECRET && export RAMP_SANDBOX_CLIENT_SECRET
    export RAMP_SANDBOX_SCOPES='{' '.join(NEEDED)}'

The app in Ramp needs those five scopes too."""


class CheckRefused(RuntimeError):
    """The check would have written outside its one vendor, or can't run safely; nothing was sent."""


class WriteGuard:
    """D1: the only writes allowed are a hold or release of one vendor, and a saved payment date for
    one of its unpaid bills (`dates`, filled once the bills are read). Records Ramp's status for each."""

    def __init__(self, vendor_id: str) -> None:
        self.vendor_id = vendor_id
        self.dates: dict[str, str] = {}
        self.writes: list[tuple[str, str, int]] = []

    @staticmethod
    def _path(request: httpx.Request) -> str:
        return request.url.path.removeprefix("/developer/v1")

    def _allowed(self, request: httpx.Request) -> bool:
        method, path = request.method, self._path(request)
        if method == "GET" or (method == "POST" and path == "/token"):
            return True
        if method == "POST":
            return path in (f"/vendors/{self.vendor_id}/hold", f"/vendors/{self.vendor_id}/release")
        if method == "PATCH" and path.startswith("/bills/"):
            saved = self.dates.get(path.removeprefix("/bills/"))
            try:
                return saved is not None and json.loads(request.content) == {"payment_date": saved}
            except ValueError:
                return False
        return False

    async def _on_request(self, request: httpx.Request) -> None:
        if not self._allowed(request):
            raise CheckRefused(f"the hold check writes only to vendor {self.vendor_id}: refused {request.method} {request.url.path}")

    async def _on_response(self, response: httpx.Response) -> None:
        request = response.request
        if request.method != "GET" and self._path(request) != "/token":
            self.writes.append((request.method, self._path(request), response.status_code))

    def client(self, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=15.0,
                                 event_hooks={"request": [self._on_request], "response": [self._on_response]})


@dataclasses.dataclass
class Report:
    vendor_id: str
    login_scopes: tuple[str, ...]
    vendor_name: str = ""
    refused: str = ""
    saved: dict[str, str | None] = dataclasses.field(default_factory=dict)  # unpaid bill: payment date before
    hold_error: str = ""
    held_after: float | None = None  # seconds until the read-back passed; None if it never did
    held_reads: int = 0
    vendor_showed_hold: bool = False  # at the last read-back
    held_bills: list[tuple[str, str | None, bool]] = dataclasses.field(default_factory=list)  # last read while held
    release_error: str = ""
    cleared_after: float | None = None
    cleared_reads: int = 0
    restore_errors: list[str] = dataclasses.field(default_factory=list)
    after: dict[str, str | None] = dataclasses.field(default_factory=dict)
    writes: list[tuple[str, str, int]] = dataclasses.field(default_factory=list)
    wait: float = 60.0

    @property
    def dates_back(self) -> bool:
        return bool(self.after) and all(self.after.get(b) == d for b, d in self.saved.items())


async def _until(test: Callable[[], Awaitable[bool]], clock: Callable[[], float],
                 sleep: Callable[[float], Awaitable[None]], wait: float) -> tuple[float | None, int]:
    """D4: one read a second until `test` passes or `wait` seconds have gone; (seconds, reads)."""
    start, reads = clock(), 0
    while True:
        reads += 1
        if await test():
            return clock() - start, reads
        if clock() - start >= wait:
            return None, reads
        await sleep(1.0)


async def hold_check(sandbox: RampSandbox, guard: WriteGuard, http: httpx.AsyncClient, *,
                     clock: Callable[[], float] = time.monotonic,
                     sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, wait: float = 60.0,
                     limiter: TokenBucket | None = None) -> Report:
    missing = [s for s in NEEDED if s not in sandbox.scopes]
    if missing:
        raise CheckRefused(f"RAMP_SANDBOX_SCOPES lacks {' '.join(missing)}; the check needs {' '.join(NEEDED)}")
    scoped = dataclasses.replace(sandbox, scopes=NEEDED)
    layer = RampSandboxAdapter(scoped.base_url, RampLogin(scoped, http), http, limiter or TokenBucket(100))  # Ramp's limit
    vendor_id = guard.vendor_id
    r = Report(vendor_id=vendor_id, login_scopes=NEEDED, wait=wait)

    vendor = await layer.get_vendor(vendor_id)
    r.vendor_name = vendor.name
    if vendor.hold_id:  # D2: maybe a person's hold; the release would undo it
        r.refused = f"this vendor is already on hold ({vendor.hold_id}), maybe by a person, so the check left it alone"
        return r
    unpaid = [b for b in await layer.list_bills_for_vendor(vendor_id) if not b.paid]
    r.saved = {b.id: b.payment_date.isoformat() if b.payment_date else None for b in unpaid}
    guard.dates = {b: d for b, d in r.saved.items() if d}

    async def held() -> bool:
        v = await layer.get_vendor(vendor_id)
        bills = [b for b in await layer.list_bills_for_vendor(vendor_id) if b.id in r.saved]
        r.held_bills = [(b.id, b.payment_date.isoformat() if b.payment_date else None, b.is_on_hold) for b in bills]
        r.vendor_showed_hold = bool(v.hold_id)
        return r.vendor_showed_hold and all(b.is_on_hold for b in bills)  # the worker's read-back rule

    async def cleared() -> bool:
        return (await layer.get_vendor(vendor_id)).hold_id is None

    try:
        try:
            await layer.place_vendor_hold(vendor_id, REASON)
            r.held_after, r.held_reads = await _until(held, clock, sleep, wait)
        except RampError as exc:
            r.hold_error = str(exc)
    finally:  # D5: release whatever happened above, even a bug
        try:
            await layer.release_vendor_hold(vendor_id)
        except RampError as exc:
            r.release_error = str(exc)
        if not r.release_error:
            r.cleared_after, r.cleared_reads = await _until(cleared, clock, sleep, wait)
            if r.cleared_after is not None:  # as the worker: dates only once the hold is gone
                for bill_id, day in guard.dates.items():
                    try:
                        await layer.set_bill_payment_date(bill_id, dt.date.fromisoformat(day))
                    except RampError as exc:
                        r.restore_errors.append(f"{bill_id}: {exc}")
                bills = await layer.list_bills_for_vendor(vendor_id)
                r.after = {b.id: b.payment_date.isoformat() if b.payment_date else None for b in bills if b.id in r.saved}
        r.writes = list(guard.writes)
    return r


def render(r: Report) -> str:
    lines = [f"Logged in to Ramp's sandbox with: {' '.join(r.login_scopes)}",
             f"Vendor {r.vendor_id}: {r.vendor_name}", ""]
    if r.refused:
        return "\n".join(lines + [f"Left alone: {r.refused}.",
                                  f"To use it, remove that hold by hand ({BY_HAND}), or pick another vendor (probe --list)."])
    lines.append("Unpaid bills before, with their payment dates")
    lines += [f"  {b}  {d or '(none)'}" for b, d in r.saved.items()] or ["  none"]
    lines += ["", "Writes, as Ramp answered"] + [f"  {m} {p}  {s}" for m, p, s in r.writes]
    lines.append("")
    if r.hold_error:
        lines.append(f"Hold: Ramp refused or couldn't be reached: {r.hold_error}")
    elif r.held_after is None:
        why = "showed the hold, but a bill below was NOT held" if r.vendor_showed_hold else "never showed the hold"
        lines.append(f"Hold: the worker's read-back did NOT pass within {r.wait:.0f} seconds ({r.held_reads} reads): "
                     f"the vendor {why}. The worker would retry the step.")
    else:
        first = "yes" if r.held_reads == 1 else "no"
        lines.append(f"Hold: showed after {r.held_after:.1f} seconds, on read {r.held_reads} "
                     f"(the worker's first read-back would have seen it: {first}; it reads {WORKER_READS} times, then retries)")
    lines += [f"  {b}  date {d or '(none)'}  {'held' if h else 'NOT held'}" for b, d, h in r.held_bills]
    if r.held_after is not None:
        lines.append("  the worker's read-back would pass")
    if r.release_error:
        lines += ["", f"Release FAILED: {r.release_error}",
                  f"The vendor is still on hold. Remove it by hand ({BY_HAND}), then set these payment dates:"]
        lines += [f"  {b}  {d}" for b, d in r.saved.items() if d] or ["  (no bill had a date)"]
        return "\n".join(lines)
    if r.cleared_after is None:
        lines += ["", (f"Release: Ramp accepted it, but the hold still showed after {r.wait:.0f} seconds. "
                       "Once it is gone, set these payment dates by hand:")]
        lines += [f"  {b}  {d}" for b, d in r.saved.items() if d] or ["  (no bill had a date)"]
        return "\n".join(lines)
    lines += ["", f"Release: gone after {r.cleared_after:.1f} seconds, on read {r.cleared_reads}"]
    lines += [f"  could not set the date again: {e}" for e in r.restore_errors]
    lines += ["", "After: payment dates now (before)"]
    lines += [f"  {b}  {r.after.get(b) or '(none)'}  ({d or '(none)'})" for b, d in r.saved.items()]
    lines.append("  every unpaid bill has its starting date back" if r.dates_back
                 else "  NOT every date came back: set the ones that differ by hand")
    return "\n".join(lines)


def run(argv: list[str] | None = None, environ: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dialback_core.ramp.holdcheck", description=__doc__.split("\n\n")[0])
    parser.add_argument("--vendor", required=True, help="the Ramp vendor id to hold and release (see probe --list)")
    parser.add_argument("--yes", action="store_true", help="really hold and release it (without this, nothing is sent)")
    args = parser.parse_args(argv)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        sandbox = _ramp_sandbox(dict(os.environ if environ is None else environ))
    except ConfigurationError as exc:
        print(exc)
        return 2
    if sandbox is None:
        print(HOW_TO)
        return 2
    if not args.yes:
        print(f"This would hold vendor {args.vendor} in Ramp's sandbox, read the hold back, release it, and set its\n"
              f"unpaid bills' payment dates again. Nothing was sent. To do it, add --yes.")
        return 2
    guard = WriteGuard(args.vendor)

    async def go() -> str:
        async with guard.client(transport) as http:
            return render(await hold_check(sandbox, guard, http))

    try:
        print(asyncio.run(go()))
    except CheckRefused as exc:
        print(f"Nothing was sent: {exc}")
        return 2
    except RampError as exc:
        print(f"Ramp refused or couldn't be reached before anything was written: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
