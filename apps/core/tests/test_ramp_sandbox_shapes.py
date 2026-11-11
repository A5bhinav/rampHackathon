"""Spec 0097: the sandbox translation layer turns Ramp's real shapes into the core's objects.

`SandboxRamp` answers like Ramp's sandbox, in the field names of Ramp's published document (written
out by hand below), and keeps state the way Ramp says it does: a vendor hold writes an audit event
and takes the vendor's unpaid bills out of scheduled payment. Every request is recorded.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

import httpx
import pytest
from conftest import FINGERPRINT_KEY, core_settings
from dialback_core.datasets import create_dataset
from dialback_core.db import system_tx
from dialback_core.detection import Keys, import_all
from dialback_core.ramp.connector import RampConnector
from dialback_core.ramp.http import TokenBucket
from dialback_core.ramp.login import RampLogin
from dialback_core.ramp.port import RampAccount, RampAuditEvent, RampBill, RampError, RampHold, RampVendor
from dialback_core.ramp.sandbox import RampSandboxAdapter
from dialback_core.settings import RampSandbox
from dialback_core.worker import Worker

pytestmark = pytest.mark.anyio

BASE = "https://sandbox.ramp.test"
SANDBOX = RampSandbox(client_id="client-for-tests", client_secret=b"sandbox-secret-for-tests-only", base_url=BASE,
                      scopes=("vendors:read", "bills:read"))
V = "6b2f8c1e-0000-4a00-8000-00000000000a"  # Acme
OTHER = "6b2f8c1e-0000-4a00-8000-00000000000b"
CLERK = "9d1e7a40-0000-4b00-8000-0000000000c1"  # a Ramp user
UTC = dt.UTC


def vendor(vendor_id: str = V, name: str = "Acme Fasteners LLC", active: bool = True) -> dict:
    """Ramp's ApiVendorDetailResource, with a few of its fields the core doesn't read."""
    return {"id": vendor_id, "name": name, "display_name": name, "is_active": active, "state": "ACTIVE",
            "approval_status": "APPROVED", "country": "US", "updated_at": "2026-11-10T09:00:00+00:00"}


def account(account_id: str, last_four: str | None, status: str = "active") -> dict:
    """Ramp's ApiVendorBankAccountResource: no routing number, no vendor id, no dates."""
    return {"id": account_id, "account_number_last_four": last_four, "status": status, "is_default": status == "active",
            "payment_method": "ACH", "currency": "USD", "account_nickname": "Operating", "proposal_id": None}


def bill(bill_id: str, payment_date: str | None, status: str = "OPEN", summary: str = "PAYMENT_SCHEDULED",
         vendor_id: str = V, paid_at: str | None = None) -> dict:
    """Ramp's Bill: the vendor is an object, and the date lives in `payment`."""
    payment = None if payment_date is None else {"id": f"pay-{bill_id}", "payment_date": payment_date,
                                                  "payment_method": "ACH", "effective_date": None}
    name = "Acme Fasteners LLC" if vendor_id == V else "Brightline Office Supply Inc"
    return {"id": bill_id, "vendor": {"id": vendor_id, "name": name}, "status": status,
            "status_summary": summary, "paid_at": paid_at, "payment": payment, "invoice_number": f"INV-{bill_id[-1]}",
            "amount": {"amount": 80000, "currency_code": "USD"}}


def event(event_id: str, event_type: str, when: str, about: str = V, actor: str | None = CLERK, details=None,
          resource: str = "Vendor / Merchant", label: str = "Acme Fasteners LLC") -> dict:
    """Ramp's AuditLogEventResource. `primary_reference` is what the event is filed under."""
    return {"id": event_id, "event_type": event_type, "event_time": when, "actor_id": actor, "actor_type": "user",
            "event_details": details if details is not None else {"description": event_type},
            "primary_reference": {"id": about, "label": label, "resource_name": resource, "url": "/x"}}


def ramp_error(status: int, message: str, code: str = "DEVELOPER_7000") -> httpx.Response:
    """Ramp's DeveloperApiErrorResponse."""
    return httpx.Response(status, json={"error_v2": {"error_code": code, "error_id": "0a1b2c3d", "message": message,
                                                     "notes": "", "additional_info": {}}})


class SandboxRamp:
    """Ramp's sandbox for one business, in Ramp's shapes, with Ramp's documented hold behavior."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.vendors = {V: vendor(), OTHER: vendor(OTHER, "Brightline Office Supply Inc")}
        self.accounts = {V: [account("acct-old", "2222", "pending_deletion"), account("acct-cur", "4444")], OTHER: []}
        self.bills = [bill("bill-1", "2026-11-12T00:00:00+00:00"), bill("bill-2", None, summary="PAYMENT_NOT_INSTRUCTED"),
                      bill("bill-3", "2026-11-01T00:00:00+00:00", status="PAID", summary="PAYMENT_COMPLETED",
                           paid_at="2026-11-01T15:00:00+00:00"),
                      bill("bill-9", "2026-11-12T00:00:00+00:00", vendor_id=OTHER)]
        self.events = [event("ev-1", "Vendor placed on hold", "2026-11-01T10:00:00+00:00"),
                       event("ev-2", "Vendor hold released", "2026-11-02T10:00:00+00:00")]
        self.hold_status = 204
        self.lag = 0  # audit-log reads before a hold or release event shows (Ramp answers 202: it may apply it later)
        self.delayed: list[list] = []  # [reads left, event]
        self.release_error: int | None = None
        self.lists_scope = True
        self.ungranted: set[str] = set()  # scopes this app isn't allowed in Ramp: left out of the login's answer
        self.bills_error: str | None = None
        self.clock = 0

    def _now(self) -> str:
        self.clock += 1
        return (dt.datetime(2026, 11, 10, 12, tzinfo=UTC) + dt.timedelta(minutes=self.clock)).isoformat()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, q = request.url.path.removeprefix("/developer/v1"), parse_qs(request.url.query.decode())
        if path == "/token":
            answer: dict = {"access_token": "test-token-1", "expires_in": 864000}
            if self.lists_scope:  # Ramp's document: "Space-separated list of scopes of the access token"
                asked = parse_qs(request.content.decode()).get("scope", [""])[0].split()
                answer["scope"] = " ".join(s for s in asked if s not in self.ungranted)
            return httpx.Response(200, json=answer)
        if path == "/vendors":
            if "start" not in q:  # two pages, to show `page.next` is followed
                return httpx.Response(200, json={"data": [self.vendors[V]], "page": {"next": f"{BASE}/developer/v1/vendors?start={V}&page_size=100"}})
            return httpx.Response(200, json={"data": [self.vendors[OTHER]], "page": {"next": None}})
        parts = path.strip("/").split("/")
        if parts[0] == "vendors" and len(parts) == 2:
            return httpx.Response(200, json=self.vendors[parts[1]])
        if parts[0] == "vendors" and parts[2] == "accounts":
            # Ramp's document: an account proposed in a vendor edit, pending approval, only with include_pending.
            pending = q.get("include_pending") == ["true"]
            data = [a for a in self.accounts[parts[1]] if pending or a["status"] != "pending_approval"]
            return httpx.Response(200, json={"data": data, "page": {"next": None}})
        if parts[0] == "vendors" and parts[2] in ("hold", "release"):
            held = parts[2] == "hold"
            if not held and self.release_error:
                return ramp_error(self.release_error, "The vendor's hold could not be released.")
            # The sandbox, as spec 0099's runs saw it: a vendor that carries `status` changes it at once,
            # and a second hold is refused. A vendor without `status` (as Ramp's document has it) doesn't.
            shown = self.vendors.get(parts[1], {})
            if "status" in shown:
                if held and shown["status"] == "ON_HOLD":
                    return ramp_error(400, "Vendor is already on hold", code="BILL_PAY_7284")
                shown["status"] = "ON_HOLD" if held else "ACTIVE"
            made = event(f"ev-{len(self.events) + len(self.delayed) + 1}",
                         "Vendor placed on hold" if held else "Vendor hold released", self._now(), about=parts[1])
            if self.lag:
                self.delayed.append([self.lag, made])
            else:
                self.events.append(made)
            if held:  # Ramp: open, unpaid bills are taken out of scheduled payment
                for b in self.bills:
                    if b["vendor"]["id"] == parts[1] and b["status"] == "OPEN":
                        b["payment"] = None
            return httpx.Response(self.hold_status, content=b"{}" if self.hold_status == 202 else b"")
        if parts == ["bills"]:
            if self.bills_error:
                return ramp_error(422, self.bills_error)
            return httpx.Response(200, json={"data": self.bills, "page": {"next": None}})  # vendor_id ignored on purpose
        if parts[0] == "bills" and request.method == "PATCH":
            b = next(b for b in self.bills if b["id"] == parts[1])
            day = json.loads(request.content)["payment_date"]
            b["payment"] = None if day is None else {"id": f"pay-{b['id']}", "payment_date": f"{day}T00:00:00+00:00"}
            return httpx.Response(200, json=b)
        if parts == ["audit-logs", "events"]:
            for waiting in self.delayed:
                waiting[0] -= 1
            self.events += [made for left, made in self.delayed if left <= 0]
            self.delayed = [waiting for waiting in self.delayed if waiting[0] > 0]
            # Ramp's document, and the sandbox (found by the probe's first run): object_id needs resource_name.
            if ("object_id" in q) != ("resource_name" in q):
                return ramp_error(422, "The resource name must also be provided when filtering by object ID.")
            wanted = set(q["event_types"][0].split(",")) if "event_types" in q else None
            since = dt.datetime.fromisoformat(q["from_date"][0]) if "from_date" in q else None
            data = [e for e in self.events  # oldest first on purpose: a layer that took the first event would be wrong
                    if ("object_id" not in q  # without object_id, the whole business
                        or (e["primary_reference"]["id"] == q["object_id"][0]
                            and e["primary_reference"]["resource_name"] == q["resource_name"][0]))
                    and (wanted is None or e["event_type"] in wanted)
                    and (since is None or dt.datetime.fromisoformat(e["event_time"]) >= since)]
            size, start = int(q.get("page_size", ["20"])[0]), int(q.get("start", ["0"])[0])  # Ramp's default page is 20
            more = start + size < len(data)
            nxt = f"{BASE}/developer/v1/audit-logs/events?{urlencode({**q, 'start': [start + size]}, doseq=True)}"
            return httpx.Response(200, json={"data": data[start:start + size], "page": {"next": nxt if more else None}})
        return httpx.Response(404, json={})

    def api(self, path_suffix: str = "") -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/developer/v1/token" and r.url.path.endswith(path_suffix)]


async def _no_sleep(_: float) -> None:
    pass


@pytest.fixture
async def ramp():
    fake = SandboxRamp()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as http:
        fake.adapter = RampSandboxAdapter(BASE, RampLogin(SANDBOX, http), http, TokenBucket(100_000), sleep=_no_sleep)
        fake.http = http
        yield fake


def query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


# Vendors -------------------------------------------------------------------------------------------

async def test_a_vendor_is_mapped_and_its_hold_is_read_from_ramps_audit_log(ramp):
    v = await ramp.adapter.get_vendor(V)
    # The newest of the two events is a release, so no hold is on.
    assert v == RampVendor(id=V, name="Acme Fasteners LLC", status="ACTIVE",
                           updated_at=dt.datetime(2026, 11, 10, 9, tzinfo=UTC), hold_id=None)
    [audit] = ramp.api("/audit-logs/events")
    assert query(audit) == {"object_id": [V], "resource_name": ["Vendor / Merchant"],
                            "event_types": ["Vendor placed on hold,Vendor hold released"], "page_size": ["100"]}


async def test_an_inactive_vendor_reads_inactive(ramp):
    ramp.vendors[V] = vendor(active=False)
    assert (await ramp.adapter.get_vendor(V)).status == "INACTIVE"


async def test_vendors_updated_since_sends_the_date_and_follows_pages(ramp):
    since = dt.datetime(2026, 11, 1, tzinfo=UTC)
    vendors = await ramp.adapter.list_vendors_updated_since(since)
    assert [v.id for v in vendors] == [V, OTHER] and all(v.hold_id is None for v in vendors)
    first, second = ramp.api("/vendors")
    assert query(first) == {"from_updated_at": ["2026-11-01T00:00:00+00:00"], "page_size": ["100"]}
    assert query(second)["start"] == [V]


# Bank accounts ------------------------------------------------------------------------------------

async def test_accounts_are_mapped_without_inventing_a_routing_number(ramp):
    accounts = await ramp.adapter.list_vendor_accounts(V)
    assert accounts == [
        RampAccount(id="acct-old", vendor_id=V, routing_number="", last4="2222", account_type=None, bank_name=None,
                    is_active=False, created_at=None),
        RampAccount(id="acct-cur", vendor_id=V, routing_number="", last4="4444", account_type=None, bank_name=None,
                    is_active=True, created_at=None)]
    [r] = ramp.api("/accounts")
    assert r.url.path == f"/developer/v1/vendors/{V}/accounts" and query(r) == {"page_size": ["100"]}


@pytest.mark.parametrize("last_four", [None, "123", "12345", "12a4"])
async def test_an_account_without_four_last_digits_fails_closed(ramp, last_four):
    ramp.accounts[V] = [account("acct-x", last_four)]
    with pytest.raises(RampError) as err:
        await ramp.adapter.list_vendor_accounts(V)
    assert not err.value.retryable


# Bills ------------------------------------------------------------------------------------------------

async def test_bills_map_the_payment_date_paid_and_hold(ramp):
    ramp.bills.append(bill("bill-4", "2026-11-15T00:00:00+00:00", summary="ON_HOLD"))
    bills = await ramp.adapter.list_bills_for_vendor(V)
    assert bills == [
        RampBill(id="bill-1", vendor_id=V, payment_date=dt.date(2026, 11, 12), status="OPEN", is_on_hold=False, paid=False),
        RampBill(id="bill-2", vendor_id=V, payment_date=None, status="OPEN", is_on_hold=True, paid=False),
        RampBill(id="bill-3", vendor_id=V, payment_date=dt.date(2026, 11, 1), status="PAID", is_on_hold=False, paid=True),
        RampBill(id="bill-4", vendor_id=V, payment_date=dt.date(2026, 11, 15), status="OPEN", is_on_hold=True, paid=False)]
    [r] = ramp.api("/bills")
    assert query(r) == {"vendor_id": [V], "page_size": ["100"]}


async def test_only_ramps_paid_status_makes_a_bill_paid(ramp):
    # An OPEN bill with a paid_at stays unpaid: the read-back skips paid bills, so trusting paid_at
    # could confirm a hold while this bill is still payable. Unpaid, it must show held (fail closed).
    ramp.bills = [bill("bill-5", "2026-11-14T00:00:00+00:00", summary="PAYMENT_PROCESSING",
                       paid_at="2026-11-14T10:00:00+00:00")]
    [b] = await ramp.adapter.list_bills_for_vendor(V)
    assert (b.paid, b.is_on_hold) == (False, False)


async def test_a_new_payment_date_is_patched_and_read_back(ramp):
    b = await ramp.adapter.set_bill_payment_date("bill-1", dt.date(2026, 11, 20))
    assert b.payment_date == dt.date(2026, 11, 20) and b.id == "bill-1"
    [r] = ramp.api("/bills/bill-1")
    assert r.method == "PATCH" and json.loads(r.content) == {"payment_date": "2026-11-20"}


# Holds: the worker confirms one only when the vendor shows it and every unpaid bill shows held -----

@pytest.mark.parametrize("status", [202, 204])
async def test_a_hold_reads_back_as_the_worker_needs_and_a_release_lifts_it(ramp, status):
    ramp.hold_status = status
    assert await ramp.adapter.place_vendor_hold(V, "Bank details changed") == RampHold(id="", vendor_id=V, placed_at=None)
    [placed] = ramp.api("/hold")
    assert placed.method == "POST" and json.loads(placed.content) == {"reason": "Bank details changed"}

    v = await ramp.adapter.get_vendor(V)
    unpaid = [b for b in await ramp.adapter.list_bills_for_vendor(V) if not b.paid]
    assert v.hold_id == "ev-3" and unpaid and all(b.is_on_hold for b in unpaid), "the worker's read-back passes"

    await ramp.adapter.release_vendor_hold(V)
    [released] = ramp.api("/release")
    assert released.method == "POST"
    assert (await ramp.adapter.get_vendor(V)).hold_id is None, "the release reads back as lifted"


# D5: the hold from the vendor's own status, which Ramp's sandbox updates at once ---------------------

async def test_a_vendors_status_says_whether_it_is_held_without_the_lagging_audit_log(ramp):
    ramp.vendors[V]["status"] = "ACTIVE"
    ramp.lag = 3  # the audit log is seconds behind, as on the sandbox
    await ramp.adapter.place_vendor_hold(V, "Bank details changed")
    assert (await ramp.adapter.get_vendor(V)).hold_id == "status:ON_HOLD", "held on the first read"
    await ramp.adapter.release_vendor_hold(V)
    assert (await ramp.adapter.get_vendor(V)).hold_id is None, "released on the first read"
    assert ramp.api("/audit-logs/events") == [], "the audit log wasn't read"


@pytest.mark.parametrize("status", ["ACTIVE", "INACTIVE", "PENDING", "UNMANAGED"])
async def test_a_known_status_other_than_on_hold_is_no_hold_whatever_the_audit_log_says(ramp, status):
    ramp.vendors[V]["status"] = status
    ramp.events.append(event("ev-late", "Vendor placed on hold", "2026-11-10T12:00:00+00:00"))  # not yet caught up
    assert (await ramp.adapter.get_vendor(V)).hold_id is None


@pytest.mark.parametrize("status", [None, "", "FROZEN", 7, "on_hold", ["ON_HOLD"]])
async def test_a_missing_or_unknown_status_falls_back_to_the_audit_log(ramp, status):
    if status is not None:
        ramp.vendors[V]["status"] = status
    ramp.events.append(event("ev-late", "Vendor placed on hold", "2026-11-10T12:00:00+00:00"))
    assert (await ramp.adapter.get_vendor(V)).hold_id == "ev-late"
    assert len(ramp.api("/audit-logs/events")) == 1


async def test_the_workers_own_read_back_passes_at_once_with_the_status_and_not_without_it(ramp):
    # Q209's stall: the audit log far behind (a lag of 10 audit reads), the worker's three quick reads.
    sleeps: list[float] = []

    async def counted(seconds: float) -> None:
        sleeps.append(seconds)

    worker = SimpleNamespace(_sleep=counted)  # _read_back_hold uses nothing else of the worker
    ramp.lag = 10
    ramp.vendors[V]["status"] = "ACTIVE"
    await ramp.adapter.place_vendor_hold(V, "Bank details changed")
    assert await Worker._read_back_hold(worker, ramp.adapter, V) == (True, 2) and sleeps == [], "first read"
    await ramp.adapter.release_vendor_hold(V)
    del ramp.vendors[V]["status"]  # the same hold, read the old way: from the audit log alone
    ramp.delayed = []  # start the second case clean: the audit log's newest is ev-2, a release
    await ramp.adapter.place_vendor_hold(V, "Bank details changed")
    assert await Worker._read_back_hold(worker, ramp.adapter, V) == (False, 0) and len(sleeps) == 3, \
        "without the status, the worker's read-back misses a hold the audit log hasn't caught up with"


async def test_a_hold_ramp_refuses_as_already_on_counts_as_placed_and_other_refusals_raise(ramp):
    ramp.vendors[V]["status"] = "ON_HOLD"  # someone, or an earlier try, already held it
    assert await ramp.adapter.place_vendor_hold(V, "Bank details changed") == RampHold(id="", vendor_id=V, placed_at=None)
    [refused] = ramp.api("/hold")
    assert refused.method == "POST"  # Ramp answered 400 BILL_PAY_7284, read as the hold being on

    def other(request: httpx.Request) -> httpx.Response:
        return ramp_error(400, "Vendor is inactive.", code="BILL_PAY_7300")

    async with httpx.AsyncClient(transport=httpx.MockTransport(other)) as http:
        adapter = RampSandboxAdapter(BASE, "token", http, TokenBucket(100_000), sleep=_no_sleep)
        with pytest.raises(RampError, match="BILL_PAY_7300"):
            await adapter.place_vendor_hold(V, "Bank details changed")


# Audit events --------------------------------------------------------------------------------------

async def test_the_bank_change_event_is_asked_for_and_answered_under_the_cores_name(ramp):
    ramp.events.append(event("ev-9", "Bill pay bank account updated", "2026-11-10T11:00:00+00:00",
                             details={"description": "Bank account updated"}))
    [e] = await ramp.adapter.get_audit_events(V, "VENDOR_BANK_ACCOUNT_UPDATED")
    assert e == RampAuditEvent(id="ev-9", event_type="VENDOR_BANK_ACCOUNT_UPDATED", actor_id=CLERK, target_id=V,
                               occurred_at=dt.datetime(2026, 11, 10, 11, tzinfo=UTC),
                               payload={"description": "Bank account updated"})
    [r] = ramp.api("/audit-logs/events")
    assert query(r) == {"object_id": [V], "resource_name": ["Vendor / Merchant"],
                        "event_types": ["Bill pay bank account updated"], "page_size": ["100"]}


async def test_a_refusal_carries_ramps_own_explanation(ramp):
    # The probe's first run showed only "HTTP 422"; Ramp's error_v2 says what was wrong.
    ramp.bills_error = "page_size must be between 2 and 100"
    with pytest.raises(RampError) as err:
        await ramp.adapter.list_bills_for_vendor(V)
    assert "page_size must be between 2 and 100" in str(err.value) and "0a1b2c3d" in str(err.value)
    assert err.value.status == 422 and not err.value.retryable


async def test_an_event_with_no_ramp_name_fails_closed(ramp):
    with pytest.raises(RampError) as err:
        await ramp.adapter.get_audit_events(V, "SOMETHING_RAMP_HAS_NO_NAME_FOR")
    assert not err.value.retryable and ramp.api() == []


# Routing and the pipeline (spec 0096 plus this layer) ---------------------------------------------

async def sandbox_dataset(pool):
    async with system_tx(pool) as conn:  # one current sandbox dataset per database (0096, D1)
        await conn.execute("update app.datasets set retired_at = now() where ramp = 'sandbox' and retired_at is null")
    return await create_dataset(pool, "dev", f"test-{uuid.uuid4().hex[:8]}", ramp="sandbox")


async def test_routing_reads_ramps_shapes_for_a_sandbox_dataset(pool, ramp):
    sandbox = await sandbox_dataset(pool)
    connector = RampConnector(core_settings(ramp_sandbox=SANDBOX), ramp.http, limiter=TokenBucket(100_000),
                              sleep=_no_sleep, pool=pool)
    ramp.vendors[V] = vendor(active=False)
    assert (await connector.for_dataset(sandbox.id).get_vendor(V)).status == "INACTIVE", "read from Ramp's is_active"


async def test_import_stores_a_sandbox_vendor_and_a_new_account_is_a_change_with_no_known_author(pool, ramp):
    d = await sandbox_dataset(pool)
    connector = RampConnector(core_settings(ramp_sandbox=SANDBOX), ramp.http, limiter=TokenBucket(100_000),
                              sleep=_no_sleep, pool=pool)
    port, keys = connector.for_dataset(d.id), Keys(FINGERPRINT_KEY)
    assert await import_all(pool, port, keys, d.id) == 2
    async with system_tx(pool) as conn:
        rows = await (await conn.execute(
            "select b.ramp_account_id, b.routing_number, b.account_last4, b.is_current from app.bank_accounts b "
            "join app.vendors v on v.id = b.vendor_id where v.dataset_id = %s and v.ramp_vendor_id = %s order by 1",
            (d.id, V))).fetchall()
    assert [tuple(r.values()) for r in rows] == [("acct-cur", "", "4444", True), ("acct-old", "", "2222", False)]

    # A new account in Ramp: detected as a change. Ramp's event names no account, so the author is unknown.
    ramp.accounts[V].append(account("acct-new", "6666"))
    ramp.events.append(event("ev-9", "Bill pay bank account updated", "2026-11-10T11:00:00+00:00"))
    await import_all(pool, port, keys, d.id)
    async with system_tx(pool) as conn:
        change = await (await conn.execute(
            "select e.changed_by, e.changed_by_ramp_user_id, n.ramp_account_id from app.bank_change_events e "
            "join app.bank_accounts n on n.id = e.new_account_id where e.dataset_id = %s", (d.id,))).fetchone()
    assert change == {"changed_by": None, "changed_by_ramp_user_id": None, "ramp_account_id": "acct-new"}
