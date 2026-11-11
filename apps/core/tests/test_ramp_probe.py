"""Spec 0098: the read-only probe of Ramp's sandbox.

The fake sandbox is spec 0097's (Ramp's field names, Ramp's documented hold behavior). Each test sets
up what a developer would have done by hand in demo.ramp.com, then reads the probe's findings.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from urllib.parse import parse_qs

import httpx
import pytest
from dialback_core.ramp.probe import (
    ProbeRefused,
    VendorRow,
    list_vendors,
    probe,
    read_only_client,
    render,
    run,
)
from test_ramp_sandbox_shapes import BASE, OTHER, SANDBOX, SandboxRamp, V, account, bill, event

pytestmark = pytest.mark.anyio

SECRET = SANDBOX.client_secret.decode()
NOW = dt.datetime(2026, 11, 10, 13, tzinfo=dt.UTC)
FIVE = ("vendors:read", "vendors:write", "bills:read", "bills:write", "audit_logs:read")


def hold_placed_by_hand(fake: SandboxRamp) -> None:
    """What Ramp records when someone holds the vendor in its dashboard (as the fake's own hold does).
    Put first, out of time order: Ramp doesn't promise an order, so the probe must sort."""
    fake.events.insert(0, event("ev-hold", "Vendor placed on hold", "2026-11-10T12:00:00+00:00"))
    for b in fake.bills:
        if b["vendor"]["id"] == V and b["status"] == "OPEN":
            b["payment"] = None


def bank_change_by_hand(fake: SandboxRamp, details: dict) -> None:
    fake.accounts[V].append(account("acct-new", "6666"))
    fake.events.append(event("ev-bank", "Bill pay bank account updated", "2026-11-10T11:00:00+00:00", details=details))


def change_pending_review(fake: SandboxRamp) -> None:
    """A bank change waiting for approval in Ramp: an account proposed in a vendor edit (D7)."""
    fake.accounts[V].append(account("acct-proposed", "6666", "pending_approval") | {"proposal_id": "prop-1"})


async def findings_from(fake: SandboxRamp, sandbox=SANDBOX, **kwargs):
    kwargs.setdefault("now", NOW)
    async with read_only_client(httpx.MockTransport(fake.handle)) as http:
        return await probe(sandbox, http, **kwargs)


def business_wide(fake: SandboxRamp) -> list[dict[str, list[str]]]:
    """The audit-log reads without an object_id (D7), as query strings."""
    qs = [parse_qs(r.url.query.decode()) for r in fake.api("/audit-logs/events")]
    return [q for q in qs if "object_id" not in q]


# Read only (D1) -------------------------------------------------------------------------------------

async def test_the_probe_only_reads():
    fake = SandboxRamp()
    hold_placed_by_hand(fake)
    await findings_from(fake)
    writes = [(r.method, r.url.path) for r in fake.requests if r.method != "GET"]
    assert writes == [("POST", "/developer/v1/token")]
    assert len(fake.requests) > 5, "it read the vendor, its accounts, bills and events"


async def test_the_probe_client_refuses_anything_but_a_read():
    fake = SandboxRamp()
    async with read_only_client(httpx.MockTransport(fake.handle)) as http:
        for method, path in [("POST", f"/developer/v1/vendors/{V}/hold"), ("PATCH", "/developer/v1/bills/bill-1"),
                             ("DELETE", f"/developer/v1/vendors/{V}")]:
            with pytest.raises(ProbeRefused):
                await http.request(method, BASE + path, json={})
    assert fake.requests == [], "nothing reached Ramp"


async def test_the_probe_asks_only_for_read_scopes():
    fake = SandboxRamp()
    f = await findings_from(fake, dataclasses.replace(SANDBOX, scopes=FIVE))
    [token] = [r for r in fake.requests if r.url.path == "/developer/v1/token"]
    assert parse_qs(token.content.decode())["scope"] == ["vendors:read bills:read audit_logs:read"]
    assert f.login_scopes == ("vendors:read", "bills:read", "audit_logs:read")


# What it answers (D3, D4) ------------------------------------------------------------------------------

async def test_with_a_hold_and_a_bank_change_made_by_hand_the_probe_answers_q132_to_q134():
    fake = SandboxRamp()
    hold_placed_by_hand(fake)
    bank_change_by_hand(fake, {"new_bank_account_id": "acct-new", "description": "Bank account updated"})
    f = await findings_from(fake)
    assert f.vendor_id == V and f.vendor is not None and f.vendor.name == "Acme Fasteners LLC"
    # Q202: the hold events filed under the vendor's id, and the hold the layer reads from them.
    assert f.hold_events == [("2026-11-01T10:00:00+00:00", "Vendor placed on hold"),
                             ("2026-11-02T10:00:00+00:00", "Vendor hold released"),
                             ("2026-11-10T12:00:00+00:00", "Vendor placed on hold")]
    assert f.hold_id == "ev-hold"
    # Q203: each unpaid bill as Ramp shows it, and whether the worker's read-back would pass.
    assert f.unpaid_bills == [("bill-1", "PAYMENT_SCHEDULED", None, True),
                              ("bill-2", "PAYMENT_NOT_INSTRUCTED", None, True)]
    assert f.readback_would_pass is True
    # Q204: the bank-change event, its detail keys, and whether it names one of the vendor's accounts.
    assert f.bank_events == [("ev-bank", "Bill pay bank account updated", ["description", "new_bank_account_id"], True)]
    assert f.layer_errors == []


async def test_without_a_hold_the_read_back_would_fail_and_an_event_without_an_account_says_so():
    fake = SandboxRamp()
    bank_change_by_hand(fake, {"description": "Bank account updated"})
    f = await findings_from(fake)
    assert f.hold_id is None and f.readback_would_pass is False
    assert f.unpaid_bills == [("bill-1", "PAYMENT_SCHEDULED", "2026-11-12", False),
                              ("bill-2", "PAYMENT_NOT_INSTRUCTED", None, True)]
    assert f.bank_events == [("ev-bank", "Bill pay bank account updated", ["description"], False)]


async def test_the_read_back_needs_both_the_vendors_hold_and_every_unpaid_bill_held():
    # Hold on, but Ramp left a bill scheduled (Q203's failure): the worker would not confirm it.
    fake = SandboxRamp()
    fake.events.append(event("ev-hold", "Vendor placed on hold", "2026-11-10T12:00:00+00:00"))
    f = await findings_from(fake)
    assert f.hold_id == "ev-hold" and f.readback_would_pass is False
    assert "read-back would fail" in render(f)
    # Every unpaid bill unscheduled, but no hold on the vendor (Q202's failure): still not confirmed.
    fake = SandboxRamp()
    fake.events = []
    for b in fake.bills:
        if b["status"] == "OPEN":
            b["payment"] = None
    f = await findings_from(fake)
    assert all(held for *_, held in f.unpaid_bills) and f.hold_id is None and f.readback_would_pass is False


async def test_an_account_the_layer_rejects_is_reported_and_the_probe_carries_on():
    fake = SandboxRamp()
    fake.accounts[V] = [account("acct-x", None)]
    f = await findings_from(fake)
    [error] = f.layer_errors
    assert "acct-x" in error and f.accounts == [] and len(f.unpaid_bills) == 2


# Where a bank change went (D7) ------------------------------------------------------------------------

async def test_pending_accounts_are_listed_with_their_status_and_the_layers_view_is_unchanged():
    fake = SandboxRamp()
    change_pending_review(fake)
    f = await findings_from(fake)
    assert f.all_accounts == [("acct-old", "2222", "pending_deletion", None), ("acct-cur", "4444", "active", None),
                              ("acct-proposed", "6666", "pending_approval", "prop-1")]
    # Spec 0098 D7 left the layer's read as it was; spec 0093 D1 then made the layer read pending accounts too.
    assert [a.id for a in f.accounts] == ["acct-old", "acct-cur", "acct-proposed"]
    text = render(f)
    assert "acct-proposed" in text and "pending_approval" in text and "prop-1" in text


async def test_recent_events_from_anywhere_in_the_business_show_where_a_change_was_filed():
    # Where Ramp files these is what the probe must find out, so the names and references below are
    # planted in several places on purpose; they are not Ramp's.
    fake = SandboxRamp()
    change_pending_review(fake)
    # Each shown event is shown for one reason only, so each rule is checked on its own.
    fake.events += [
        event("ev-appr2", "Approval step approved", "2026-11-10T11:40:00+00:00", about="appr-2", resource="Approval",
              label="Card limit", details={}),  # an approval type only
        event("ev-appr", "Approval step added", "2026-11-10T11:30:00+00:00", about="appr-1", resource="Approval",
              label="Vendor edit", details={"proposal_id": "prop-1"}),  # an approval type; names the proposal
        event("ev-draft", "Draft vendor changed by AP coworker", "2026-11-10T11:25:00+00:00", about="draft-1",
              resource="Draft vendor", label="Draft",
              details={"summary": "proposed bank acct-proposed for review"}),  # only the account id, inside a sentence
        event("ev-edit", "Edited vendor field", "2026-11-10T11:20:00+00:00", label="Acme",
              details={"field": "payment details"}),  # only the vendor's id, as what it is filed under
        event("ev-own", "Bill pay rejected PDR bank account ownership match", "2026-11-10T11:10:00+00:00",
              about="bpv-1", resource="Bill pay vendor", label="ACME FASTENERS LLC"),  # the name, another case
        event("ev-other", "Bank account updated", "2026-11-10T10:00:00+00:00", about=OTHER,
              label="Brightline Office Supply Inc"),  # a bank type, another vendor
        event("ev-card", "Card created", "2026-11-10T12:00:00+00:00", about="card-1", resource="Card",
              label="Ops card"),  # neither: left out
        event("ev-funds", "Transferred ownership of funds", "2026-11-10T12:10:00+00:00", about="fund-1",
              resource="Funds", label="Travel"),  # "ownership" alone: left out
        event("ev-old", "Bill pay bank account updated", "2026-11-08T10:00:00+00:00"),  # before the window
    ]
    f = await findings_from(fake)
    assert [(e.id, e.kind, e.resource, e.ref_id, e.label, e.actor_type, e.detail_keys, e.mentions_vendor)
            for e in f.recent_events] == [
        ("ev-appr2", "Approval step approved", "Approval", "appr-2", "Card limit", "user", (), False),
        ("ev-appr", "Approval step added", "Approval", "appr-1", "Vendor edit", "user", ("proposal_id",), True),
        ("ev-draft", "Draft vendor changed by AP coworker", "Draft vendor", "draft-1", "Draft", "user", ("summary",), True),
        ("ev-edit", "Edited vendor field", "Vendor / Merchant", V, "Acme", "user", ("field",), True),
        ("ev-own", "Bill pay rejected PDR bank account ownership match", "Bill pay vendor", "bpv-1",
         "ACME FASTENERS LLC", "user", ("description",), True),
        ("ev-other", "Bank account updated", "Vendor / Merchant", OTHER, "Brightline Office Supply Inc", "user",
         ("description",), False)]
    assert f.recent_events[0].when == "2026-11-10T11:40:00+00:00"
    [q] = business_wide(fake)
    assert q["from_date"] == ["2026-11-09T13:00:00Z"], "24 hours before now"
    assert "resource_name" not in q and "event_types" not in q, "nothing filtered at Ramp but the time"
    assert f.recent_truncated is False
    text = render(f)
    assert "ev-appr" in text and "Approval appr-1" in text and "ev-card" not in text


async def test_recent_events_stop_after_ten_pages_and_say_so():
    noise = [event(f"ev-n{i}", "Card created", "2026-11-10T12:00:00+00:00", about=f"card-{i}", resource="Card",
                   label="Ops card") for i in range(1000)]
    late = event("ev-late", "Bank account updated", "2026-11-10T12:30:00+00:00")
    fake = SandboxRamp()
    fake.events += noise[:999] + [late]  # 1,000 in the window: exactly ten pages of 100
    f = await findings_from(fake)
    assert [e.id for e in f.recent_events] == ["ev-late"] and f.recent_truncated is False
    assert len(business_wide(fake)) == 10 and "stopped after" not in render(f)
    fake = SandboxRamp()
    fake.events += noise + [late]  # 1,001: the bank event is on an eleventh page, which isn't read
    f = await findings_from(fake)
    assert f.recent_events == [] and f.recent_truncated is True
    assert len(business_wide(fake)) == 10
    assert "stopped after 1000 events" in render(f)


def test_since_sets_the_window(capsys):
    fake = SandboxRamp()
    env = {"RAMP_SANDBOX_CLIENT_ID": "client-for-tests", "RAMP_SANDBOX_CLIENT_SECRET": SECRET}
    before = dt.datetime.now(dt.UTC)
    assert run(["--skip-doc", "--since", "48"], env, transport=httpx.MockTransport(fake.handle)) == 0
    [q] = business_wide(fake)
    sent = dt.datetime.fromisoformat(q["from_date"][0])
    assert abs(sent - (before - dt.timedelta(hours=48))) < dt.timedelta(minutes=1)
    assert "last 48 hours" in capsys.readouterr().out


def schema(*fields: str, nullable: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": {k: {"type": "string", **({"nullable": True} if k in nullable else {})} for k in fields}}


async def test_the_probe_lists_where_ramps_answers_differ_from_its_document():
    vendor_fields = ("id", "name", "display_name", "is_active", "state", "approval_status", "country", "updated_at")
    doc = {"components": {"schemas": {
        "Vendor": schema(*vendor_fields, nullable=("updated_at",)),
        "ApiVendorDetailResource": schema(*(f for f in vendor_fields if f != "country"), nullable=("updated_at",)),  # planted
        "ApiVendorBankAccountResource": schema("id", "account_number_last_four", "status", "is_default", "payment_method",
                                               "currency", "account_nickname", "proposal_id",
                                               nullable=("account_number_last_four", "proposal_id")),
        "Bill": schema("id", "vendor", "status", "status_summary", "paid_at", "payment", "invoice_number", "amount",
                       nullable=("paid_at", "payment")),
        "AuditLogEventResource": schema("id", "event_type", "event_time", "actor_id", "actor_type", "event_details",
                                        "primary_reference", nullable=("actor_id", "primary_reference"))}}}
    fake = SandboxRamp()
    fake.vendors[V]["display_name"] = None  # planted: Ramp sends a null its document says it won't
    f = await findings_from(fake, doc=doc)
    assert f.doc_differences == ["ApiVendorDetailResource.country: not in Ramp's document",
                                 "ApiVendorDetailResource.display_name: null, which Ramp's document says it can't be",
                                 "Vendor.display_name: null, which Ramp's document says it can't be"]


# --list (D6): which vendor to look at -------------------------------------------------------------------

async def rows_from(fake: SandboxRamp, **kwargs) -> list[VendorRow]:
    async with read_only_client(httpx.MockTransport(fake.handle)) as http:
        return await list_vendors(SANDBOX, http, **kwargs)


async def test_list_shows_vendors_with_unpaid_bills_most_first_with_their_ids():
    # By hand from the fake: Acme has two unpaid bills (one paid) and one active account of two;
    # Brightline has one unpaid bill and no account.
    fake = SandboxRamp()
    assert await rows_from(fake) == [VendorRow(V, "Acme Fasteners LLC", unpaid_bills=2, active_accounts=1),
                                     VendorRow(OTHER, "Brightline Office Supply Inc", unpaid_bills=1, active_accounts=0)]
    assert {r.method for r in fake.requests} == {"GET", "POST"}  # the login's POST, then reads
    assert [r.url.path for r in fake.requests if r.method == "POST"] == ["/developer/v1/token"]


async def test_list_leaves_out_vendors_with_no_unpaid_bills_and_stops_at_the_limit():
    fake = SandboxRamp()
    fake.bills.insert(0, bill("bill-8", "2026-11-12T00:00:00+00:00", vendor_id=OTHER))  # Ramp's order puts Brightline first
    assert [r.id for r in await rows_from(fake, limit=1)] == [V], "two each, so the tie goes by id, then the limit"
    fake = SandboxRamp()
    fake.bills = [b for b in fake.bills if b["vendor"]["id"] == V]
    assert [r.id for r in await rows_from(fake)] == [V], "Brightline has no unpaid bill"


def test_the_list_command_prints_each_vendors_id(capsys):
    fake = SandboxRamp()
    env = {"RAMP_SANDBOX_CLIENT_ID": "client-for-tests", "RAMP_SANDBOX_CLIENT_SECRET": SECRET}
    assert run(["--list"], env, transport=httpx.MockTransport(fake.handle)) == 0
    out = capsys.readouterr().out
    assert V in out and OTHER in out and "Brightline Office Supply Inc" in out and "--vendor" in out
    assert SECRET not in out and "test-token-1" not in out


# The command (D2) --------------------------------------------------------------------------------------

def test_missing_credentials_say_how_to_set_them(capsys):
    assert run([], {}) == 2
    out = capsys.readouterr().out
    assert "RAMP_SANDBOX_CLIENT_ID" in out and "read -rs RAMP_SANDBOX_CLIENT_SECRET" in out


def test_the_output_never_shows_the_secret_or_the_token(capsys):
    fake = SandboxRamp()
    hold_placed_by_hand(fake)
    env = {"RAMP_SANDBOX_CLIENT_ID": "client-for-tests", "RAMP_SANDBOX_CLIENT_SECRET": SECRET}
    assert run(["--skip-doc"], env, transport=httpx.MockTransport(fake.handle)) == 0
    out = capsys.readouterr().out
    assert "Acme Fasteners LLC" in out and "read-back would pass" in out
    assert SECRET not in out and "test-token-1" not in out


async def test_render_shows_each_question():
    fake = SandboxRamp()
    hold_placed_by_hand(fake)
    text = render(await findings_from(fake))
    for heading in ("Q202", "Q203", "Q204", "Ramp's document"):
        assert heading in text
