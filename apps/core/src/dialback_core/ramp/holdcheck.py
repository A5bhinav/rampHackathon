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
from typing import Any

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
    one of its unpaid bills (`dates`, filled once the bills are read). Records Ramp's status for each
    write, and each bill's `status_summary` from the bill reads the layer makes (D9: no extra request)."""

    def __init__(self, vendor_id: str) -> None:
        self.vendor_id = vendor_id
        self.dates: dict[str, str] = {}
        self.writes: list[tuple[str, str, int]] = []
        self.statuses: dict[str, str | None] = {}
        self.vendor_raw: dict[str, Any] = {}  # Ramp's last answer for this vendor, as sent (D10)

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
        path = self._path(request)
        if request.method != "GET" or path not in ("/bills", f"/vendors/{self.vendor_id}"):
            return
        await response.aread()
        try:
            body = response.json()
        except ValueError:
            return
        if not isinstance(body, dict):
            return
        if path != "/bills":  # an error answer is followed by a retry or a RampError, so the last is kept
            self.vendor_raw = body
            return
        data = body.get("data")
        for b in data if isinstance(data, list) else []:
            if isinstance(b, dict) and "id" in b:
                self.statuses[str(b["id"])] = b.get("status_summary")

    def client(self, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=15.0,
                                 event_hooks={"request": [self._on_request], "response": [self._on_response]})


@dataclasses.dataclass(frozen=True)
class Look:
    """D10: one read-back, as each part of Ramp's answer showed it."""
    read: int
    at: float  # seconds after Ramp answered the hold or release, when this read started
    audit_hold: bool  # the audit log's newest hold event is a placement (the layer's reading)
    vendor_changes: tuple[tuple[str, str], ...]  # vendor fields that differ from before the hold
    bills: tuple[tuple[str, str | None, str | None], ...]  # (bill, status_summary, payment date)


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _day(bill: Any) -> str | None:
    return bill.payment_date.isoformat() if bill.payment_date else None


@dataclasses.dataclass
class Report:
    vendor_id: str
    login_scopes: tuple[str, ...]
    granted: tuple[str, ...] | None = None  # as Ramp's login answer listed them
    vendor_name: str = ""
    second_hold: str = ""  # "", "accepted", or "refused: ..."
    refused: str = ""
    saved: dict[str, str | None] = dataclasses.field(default_factory=dict)  # unpaid bill: payment date before
    status_before: dict[str, str | None] = dataclasses.field(default_factory=dict)  # Ramp's status_summary (D9)
    status_held: dict[str, str | None] = dataclasses.field(default_factory=dict)  # at the last read-back
    status_after: dict[str, str | None] = dataclasses.field(default_factory=dict)
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
    hold_looks: list[Look] = dataclasses.field(default_factory=list)  # D10
    release_looks: list[Look] = dataclasses.field(default_factory=list)

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
    login = RampLogin(scoped, http)
    layer = RampSandboxAdapter(scoped.base_url, login, http, limiter or TokenBucket(100))  # Ramp's limit
    vendor_id = guard.vendor_id
    r = Report(vendor_id=vendor_id, login_scopes=NEEDED, wait=wait)
    await login.token()
    r.granted = login.granted
    if r.granted is not None and (ungranted := [s for s in NEEDED if s not in r.granted]):
        # Found on the sandbox: an app without bills:write held and released, then couldn't set the dates back.
        raise CheckRefused(f"Ramp granted only {' '.join(r.granted)}; enable {' '.join(ungranted)} for this app "
                           "in Ramp's developer settings, then run again")

    vendor = await layer.get_vendor(vendor_id)
    r.vendor_name = vendor.name
    if vendor.hold_id:  # D2: maybe a person's hold; the release would undo it
        r.refused = f"this vendor is already on hold ({vendor.hold_id}), maybe by a person, so the check left it alone"
        return r
    unpaid = [b for b in await layer.list_bills_for_vendor(vendor_id) if not b.paid]
    r.saved = {b.id: b.payment_date.isoformat() if b.payment_date else None for b in unpaid}
    r.status_before = {b: guard.statuses.get(b) for b in r.saved}
    guard.dates = {b: d for b, d in r.saved.items() if d}
    base = dict(guard.vendor_raw)  # the vendor before the hold, for D10's changed fields
    written = [0.0]  # when Ramp answered the last hold or release

    async def look(looks: list[Look]) -> tuple[Any, list[Any]]:
        """D10: one read of the vendor (and so the audit log) and its bills, as each part showed it."""
        at = clock() - written[0]
        v = await layer.get_vendor(vendor_id)
        bills = [b for b in await layer.list_bills_for_vendor(vendor_id) if b.id in r.saved]
        raw = guard.vendor_raw
        changes = tuple((k, _short(raw.get(k))) for k in sorted(set(raw) | set(base)) if raw.get(k) != base.get(k))
        looks.append(Look(len(looks) + 1, at, bool(v.hold_id), changes,
                          tuple((b.id, guard.statuses.get(b.id), _day(b)) for b in bills)))
        return v, bills

    async def held() -> bool:
        v, bills = await look(r.hold_looks)
        r.held_bills = [(b.id, _day(b), b.is_on_hold) for b in bills]
        r.vendor_showed_hold = bool(v.hold_id)
        return r.vendor_showed_hold and all(b.is_on_hold for b in bills)  # the worker's read-back rule

    async def cleared() -> bool:
        v, _ = await look(r.release_looks)
        return v.hold_id is None

    try:
        try:
            await layer.place_vendor_hold(vendor_id, REASON)
            written[0] = clock()
            r.held_after, r.held_reads = await _until(held, clock, sleep, wait)
            r.status_held = {b: guard.statuses.get(b) for b in r.saved}
        except RampError as exc:
            r.hold_error = str(exc)
        if r.held_after is not None:  # the worker's retry places the hold again on a vendor already held
            try:
                await layer.place_vendor_hold(vendor_id, REASON)
                r.second_hold = "accepted"
            except RampError as exc:
                r.second_hold = f"refused: {exc}"
    finally:  # D5: release whatever happened above, even a bug
        try:
            await layer.release_vendor_hold(vendor_id)
            written[0] = clock()
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
                r.status_after = {b: guard.statuses.get(b) for b in r.saved}
        r.writes = list(guard.writes)
    return r


def _timeline(title: str, looks: list[Look]) -> list[str]:
    """D10: one line per read-back."""
    if not looks:
        return []
    lines = ["", f"Read by read {title} (seconds after Ramp's answer, when each read started)"]
    for look in looks:
        vendor = ", ".join(f"{k}={v}" for k, v in look.vendor_changes) or "as before the hold"
        bills = ", ".join(f"{b} {s or '(status unknown)'} ({d or 'none'})" for b, s, d in look.bills) or "none"
        lines.append(f"  read {look.read}  +{look.at:.1f} s  audit log: {'hold' if look.audit_hold else 'no hold'}  "
                     f"vendor: {vendor}  bills: {bills}")
    return lines


def render(r: Report) -> str:
    lines = [f"Logged in to Ramp's sandbox with: {' '.join(r.login_scopes)}",
             f"Ramp granted: {' '.join(r.granted)}" if r.granted is not None
             else "Ramp's login answer listed no scopes, so the check couldn't confirm them before writing",
             f"Vendor {r.vendor_id}: {r.vendor_name}", ""]
    if r.refused:
        return "\n".join(lines + [f"Left alone: {r.refused}.",
                                  f"To use it, remove that hold by hand ({BY_HAND}), or pick another vendor (probe --list)."])
    def status(table: dict[str, str | None], b: str) -> str:
        return table.get(b) or "(status unknown)"

    def last_seen(b: str) -> str:
        return r.status_after.get(b) or r.status_held.get(b) or r.status_before.get(b) or "unknown"

    lines.append("Unpaid bills before: payment date, Ramp's status")
    lines += [f"  {b}  {d or '(none)'}  {status(r.status_before, b)}" for b, d in r.saved.items()] or ["  none"]
    lines += ["", "Writes, as Ramp answered"] + [f"  {m} {p}  {s}" for m, p, s in r.writes]
    lines.append("")
    if r.hold_error:
        lines.append(f"Hold: Ramp refused or couldn't be reached: {r.hold_error}")
    elif r.held_after is None:
        why = "showed the hold, but a bill below was NOT held" if r.vendor_showed_hold else "never showed the hold"
        lines.append(f"Hold: the worker's read-back did NOT pass within {r.wait:.0f} seconds ({r.held_reads} reads): "
                     f"the vendor {why}. The worker would retry the step.")
    else:
        # The worker's reads are 0.2 and 0.4 s apart, this check's 1 s: its last read starts before this
        # check's third, so a hold seen on read 2 or 3 may or may not have been seen by it.
        first = "yes" if r.held_reads == 1 else "maybe" if r.held_reads <= WORKER_READS else "no"
        lines.append(f"Hold: showed after {r.held_after:.1f} seconds, on read {r.held_reads} (the worker's first "
                     f"read-back would have seen it: {first}; it reads {WORKER_READS} times, 0.2 and 0.4 seconds "
                     "apart, then retries the step)")
    lines += [f"  {b}  date {d or '(none)'}  {'held' if h else 'NOT held'}  {status(r.status_held, b)}"
              for b, d, h in r.held_bills]
    if r.held_after is not None:
        lines.append("  the worker's read-back would pass")
    if r.second_hold == "accepted":
        lines.append("A second hold on the held vendor (the worker's retry sends one): Ramp accepted it")
    elif r.second_hold:
        lines.append(f"A second hold on the held vendor (the worker's retry sends one): Ramp REFUSED it "
                     f"({r.second_hold.removeprefix('refused: ')}); a worker retry after a slow read-back would fail")
    lines += _timeline("after the hold", r.hold_looks)
    lines += _timeline("after the release", r.release_looks)
    if r.release_error:
        lines += ["", f"Release FAILED: {r.release_error}",
                  f"The vendor is still on hold. Remove it by hand ({BY_HAND}), then set these payment dates:"]
        lines += [f"  {b}  {d}  (Ramp's status last seen: {last_seen(b)})" for b, d in r.saved.items() if d] \
            or ["  (no bill had a date)"]
        return "\n".join(lines)
    if r.cleared_after is None:
        lines += ["", (f"Release: Ramp accepted it, but the hold still showed after {r.wait:.0f} seconds. "
                       "Once it is gone, set these payment dates by hand:")]
        lines += [f"  {b}  {d}  (Ramp's status last seen: {last_seen(b)})" for b, d in r.saved.items() if d] \
            or ["  (no bill had a date)"]
        return "\n".join(lines)
    lines += ["", f"Release: gone after {r.cleared_after:.1f} seconds, on read {r.cleared_reads}"]
    lines += [f"  could not set the date again: {e}" for e in r.restore_errors]
    lines += ["", "After: payment date and Ramp's status now (before)"]
    lines += [f"  {b}  {r.after.get(b) or '(none)'}  {status(r.status_after, b)}  "
              f"(before: {d or '(none)'}  {status(r.status_before, b)})" for b, d in r.saved.items()]
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
        print(f"Nothing was written: {exc}")
        return 2
    except RampError as exc:
        print(f"Ramp refused or couldn't be reached before anything was written: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
