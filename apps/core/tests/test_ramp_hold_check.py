"""Spec 0099: hold and release one sandbox vendor the way the worker does, and leave it as found.

The fake sandbox is spec 0097's: a hold writes an event and clears the vendor's open bills' payment
dates; a release writes an event and gives no date back (Ramp's help center says the same).
"""
from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
from dialback_core.ramp.holdcheck import CheckRefused, WriteGuard, hold_check, render, run
from dialback_core.ramp.http import TokenBucket
from test_ramp_sandbox_shapes import BASE, OTHER, SANDBOX, SandboxRamp, V, event, ramp_error

pytestmark = pytest.mark.anyio

SECRET = SANDBOX.client_secret.decode()
FIVE = ("vendors:read", "vendors:write", "bills:read", "bills:write", "audit_logs:read")
ENV = {"RAMP_SANDBOX_CLIENT_ID": "client-for-tests", "RAMP_SANDBOX_CLIENT_SECRET": SECRET,
       "RAMP_SANDBOX_SCOPES": " ".join(FIVE)}


class Clock:
    """Seconds that pass only when the check sleeps."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


async def checked(fake: SandboxRamp, scopes=FIVE, wait: float = 60.0, handle=None):
    guard, clock = WriteGuard(V), Clock()
    async with guard.client(httpx.MockTransport(handle or fake.handle)) as http:
        report = await hold_check(dataclasses.replace(SANDBOX, scopes=scopes), guard, http,
                                  clock=clock.now, sleep=clock.sleep, wait=wait,
                                  limiter=TokenBucket(10_000))  # Ramp's 100 per 10 s would really wait here
    return report


def writes(fake: SandboxRamp) -> list[tuple[str, str]]:
    return [(r.method, r.url.path.removeprefix("/developer/v1")) for r in fake.requests if r.method != "GET"]


# D1, D2: what it may write, and when it writes nothing ----------------------------------------------

async def test_the_client_refuses_writes_outside_this_vendor_and_its_saved_dates():
    fake = SandboxRamp()
    guard = WriteGuard(V)
    guard.dates = {"bill-1": "2026-11-12"}
    async with guard.client(httpx.MockTransport(fake.handle)) as http:
        for method, path, body in [("POST", f"/vendors/{OTHER}/hold", {}),
                                   ("PATCH", "/bills/bill-9", {"payment_date": "2026-11-12"}),  # another vendor's bill
                                   ("PATCH", "/bills/bill-1", {"payment_date": "2026-12-01"}),  # not the saved date
                                   ("POST", f"/vendors/{V}/update-bank-accounts", {}),
                                   ("DELETE", f"/vendors/{V}", None)]:
            with pytest.raises(CheckRefused):
                await http.request(method, f"{BASE}/developer/v1{path}", json=body)
        assert fake.requests == [], "nothing reached Ramp"
        await http.patch(f"{BASE}/developer/v1/bills/bill-1", json={"payment_date": "2026-11-12"})
    assert writes(fake) == [("PATCH", "/bills/bill-1")], "the saved date for this vendor's bill is allowed"


async def test_without_a_needed_scope_nothing_reaches_ramp():
    fake = SandboxRamp()
    with pytest.raises(CheckRefused, match="bills:write"):
        await checked(fake, scopes=("vendors:read", "vendors:write", "bills:read", "audit_logs:read"))
    assert fake.requests == []


async def test_a_vendor_already_on_hold_is_left_alone():
    fake = SandboxRamp()
    fake.events.append(event("ev-hand", "Vendor placed on hold", "2026-11-10T09:00:00+00:00"))  # a person's hold
    report = await checked(fake)
    assert writes(fake) == [("POST", "/token")] and report.refused
    assert "Remove hold" in render(report)


# D3 to D6: the round trip ---------------------------------------------------------------------------

async def test_a_round_trip_holds_releases_and_gives_every_date_back():
    fake = SandboxRamp()
    fake.hold_status = 202
    report = await checked(fake)
    assert writes(fake) == [("POST", "/token"), ("POST", f"/vendors/{V}/hold"), ("POST", f"/vendors/{V}/release"),
                            ("PATCH", "/bills/bill-1")]
    [patch] = [r for r in fake.requests if r.method == "PATCH"]
    assert json.loads(patch.content) == {"payment_date": "2026-11-12"}
    assert report.writes == [("POST", f"/vendors/{V}/hold", 202), ("POST", f"/vendors/{V}/release", 202),
                             ("PATCH", "/bills/bill-1", 200)]
    # bill-3 is paid and bill-9 is another vendor's: neither is touched or reported.
    assert report.saved == {"bill-1": "2026-11-12", "bill-2": None}
    assert (report.held_after, report.held_reads) == (0.0, 1)
    assert report.held_bills == [("bill-1", None, True), ("bill-2", None, True)]
    assert (report.cleared_after, report.cleared_reads) == (0.0, 1)
    assert report.after == {"bill-1": "2026-11-12", "bill-2": None} and report.dates_back
    text = render(report)
    assert "read-back would pass" in text and "every unpaid bill has its starting date back" in text


async def test_a_hold_that_shows_late_is_waited_for_and_timed():
    fake = SandboxRamp()
    fake.lag = 3  # each event shows on the third audit-log read after it
    report = await checked(fake)
    assert (report.held_after, report.held_reads) == (2.0, 3)
    assert (report.cleared_after, report.cleared_reads) == (2.0, 3)
    assert report.dates_back
    assert "the worker's first read-back would have seen it: no" in render(report)


async def test_a_hold_that_never_shows_is_a_failure_and_the_release_and_dates_still_run():
    fake = SandboxRamp()
    fake.lag = 10_000
    report = await checked(fake, wait=60.0)
    assert report.held_after is None and report.held_reads == 61 and not report.vendor_showed_hold
    assert writes(fake)[1:] == [("POST", f"/vendors/{V}/hold"), ("POST", f"/vendors/{V}/release"),
                                ("PATCH", "/bills/bill-1")]
    assert report.dates_back
    text = render(report)
    assert "read-back did NOT pass within 60 seconds" in text and "never showed the hold" in text


async def test_a_bill_ramp_leaves_scheduled_fails_the_read_back_and_the_check_still_cleans_up():
    fake = SandboxRamp()

    def handle(request: httpx.Request) -> httpx.Response:
        response = fake.handle(request)
        if request.url.path.endswith("/hold"):  # Ramp held the vendor but left bill-1 scheduled (Q203's failure)
            bill_1 = next(b for b in fake.bills if b["id"] == "bill-1")
            bill_1["payment"] = {"id": "pay-bill-1", "payment_date": "2026-11-12T00:00:00+00:00"}
        return response

    report = await checked(fake, handle=handle)
    assert report.held_after is None and report.vendor_showed_hold
    assert ("bill-1", "2026-11-12", False) in report.held_bills
    assert writes(fake)[2:] == [("POST", f"/vendors/{V}/release"), ("PATCH", "/bills/bill-1")]
    text = render(report)
    assert "the vendor showed the hold, but a bill below was NOT held" in text and "bill-1  date 2026-11-12  NOT held" in text


async def test_a_refused_release_sets_no_date_and_says_how_to_remove_the_hold():
    fake = SandboxRamp()
    fake.release_error = 422
    report = await checked(fake)
    assert writes(fake) == [("POST", "/token"), ("POST", f"/vendors/{V}/hold"), ("POST", f"/vendors/{V}/release")]
    assert "422" in report.release_error and not report.dates_back
    assert report.cleared_reads == 0, "a refused release isn't waited for"
    text = render(report)
    assert "Remove hold" in text and "bill-1  2026-11-12" in text
    fake = SandboxRamp()
    fake.release_error = 422
    fake.bills = [b for b in fake.bills if b["id"] != "bill-1"]  # no unpaid bill has a date: still not "back"
    assert not (await checked(fake)).dates_back


async def test_a_date_ramp_refuses_to_set_again_is_reported():
    fake = SandboxRamp()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":  # e.g. Ramp refuses a payment date that is now in the past
            fake.requests.append(request)
            return ramp_error(422, "The payment date must be in the future.")
        return fake.handle(request)

    report = await checked(fake, handle=handle)
    [error] = report.restore_errors
    assert error.startswith("bill-1: ") and "The payment date must be in the future." in error
    assert report.after == {"bill-1": None, "bill-2": None} and not report.dates_back
    text = render(report)
    assert "could not set the date again: bill-1" in text and "NOT every date came back" in text


async def test_a_release_ramp_accepts_but_never_shows_sets_no_date_and_says_so():
    fake = SandboxRamp()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release"):  # accepted, but the hold stays
            fake.requests.append(request)
            return httpx.Response(204)
        return fake.handle(request)

    report = await checked(fake, handle=handle)
    assert writes(fake)[1:] == [("POST", f"/vendors/{V}/hold"), ("POST", f"/vendors/{V}/release")]
    assert report.cleared_after is None and report.cleared_reads == 61 and not report.dates_back
    text = render(report)
    assert "the hold still showed after 60 seconds" in text and "bill-1  2026-11-12" in text


# The command -----------------------------------------------------------------------------------------

def test_without_yes_it_says_what_it_would_do_and_sends_nothing(capsys):
    fake = SandboxRamp()
    assert run(["--vendor", V], ENV, transport=httpx.MockTransport(fake.handle)) == 2
    assert fake.requests == [] and "--yes" in capsys.readouterr().out


def test_with_yes_it_runs_and_never_shows_the_secret_or_the_token(capsys):
    fake = SandboxRamp()
    assert run(["--vendor", V, "--yes"], ENV, transport=httpx.MockTransport(fake.handle)) == 0
    out = capsys.readouterr().out
    assert "Acme Fasteners LLC" in out and "every unpaid bill has its starting date back" in out
    assert SECRET not in out and "test-token-1" not in out
