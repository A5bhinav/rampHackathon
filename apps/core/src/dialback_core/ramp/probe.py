"""A read-only look at Ramp's sandbox, to check what the translation layer assumes (spec 0098).

    export RAMP_SANDBOX_CLIENT_ID='...'
    read -rs RAMP_SANDBOX_CLIENT_SECRET && export RAMP_SANDBOX_CLIENT_SECRET
    uv run --package dialback-core python -m dialback_core.ramp.probe [--vendor <ramp vendor id>]

It reads one vendor (its details, bank accounts, bills and audit events) through the real layer
(spec 0097) and reports what the core would see, where Ramp's answers differ from its document, and
what the sandbox says about Q202 to Q204. To see a hold or a bank change, make it by hand in
demo.ramp.com first. Read only, twice over: the token asks only for read scopes, and the client
refuses anything but a GET and the login. No secret or token is ever printed.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from ..settings import ConfigurationError, RampSandbox, _ramp_sandbox
from .http import API, TokenBucket
from .login import RampLogin
from .port import RampAccount, RampBill, RampError, RampVendor
from .sandbox import PLACED, RELEASED, VENDOR_RESOURCE, RampSandboxAdapter

DOC_PATH = Path(__file__).resolve().parents[5] / "packages" / "contracts" / "openapi" / "ramp-developer-api.json"
DOC_URL = "https://docs.ramp.com/openapi/developer-api.json"
HOW_TO = """No Ramp sandbox credentials in this terminal. Set them first (the secret isn't shown or saved):

    export RAMP_SANDBOX_CLIENT_ID='your sandbox app's client id'
    read -rs RAMP_SANDBOX_CLIENT_SECRET && export RAMP_SANDBOX_CLIENT_SECRET

For a read-only app, also: export RAMP_SANDBOX_SCOPES='vendors:read bills:read audit_logs:read'"""


class ProbeRefused(RuntimeError):
    """The probe tried something other than a read, and its client stopped it (D1)."""


async def _reads_only(request: httpx.Request) -> None:
    if request.method != "GET" and not (request.method == "POST" and request.url.path == "/developer/v1/token"):
        raise ProbeRefused(f"the probe only reads: refused {request.method} {request.url.path}")


def read_only_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, timeout=15.0, event_hooks={"request": [_reads_only]})


@dataclasses.dataclass
class Findings:
    vendor_id: str
    login_scopes: tuple[str, ...]
    vendor: RampVendor | None = None
    accounts: list[RampAccount] = dataclasses.field(default_factory=list)
    bills: list[RampBill] = dataclasses.field(default_factory=list)
    layer_errors: list[str] = dataclasses.field(default_factory=list)
    doc_note: str = ""
    doc_differences: list[str] = dataclasses.field(default_factory=list)
    hold_events: list[tuple[str, str]] = dataclasses.field(default_factory=list)  # (time, type), oldest first
    hold_id: str | None = None
    unpaid_bills: list[tuple[str, str | None, str | None, bool]] = dataclasses.field(default_factory=list)
    readback_would_pass: bool = False
    bank_events: list[tuple[str, str, list[str], bool]] = dataclasses.field(default_factory=list)


def _differences(name: str, obj: dict[str, Any], doc: dict[str, Any]) -> set[str]:
    """Top-level fields only: one the document doesn't define, and a null where it says not nullable."""
    schema = doc["components"]["schemas"].get(name, {})
    props: dict[str, Any] = dict(schema.get("properties", {}))
    for part in schema.get("allOf", []):
        props |= part.get("properties", {})
    found = set()
    for key, value in obj.items():
        if key not in props:
            found.add(f"{name}.{key}: not in Ramp's document")
        elif value is None and not props[key].get("nullable"):
            found.add(f"{name}.{key}: null, which Ramp's document says it can't be")
    return found


def _reader(sandbox: RampSandbox, http: httpx.AsyncClient) -> tuple[RampSandbox, RampLogin, RampSandboxAdapter]:
    """D1: a login that asks Ramp only for the read scopes, and the layer on top of it."""
    read = dataclasses.replace(sandbox, scopes=tuple(s for s in sandbox.scopes if s.endswith(":read")))
    login = RampLogin(read, http)
    return read, login, RampSandboxAdapter(read.base_url, login, http, TokenBucket(100))


async def probe(sandbox: RampSandbox, http: httpx.AsyncClient, *, vendor_id: str | None = None,
                doc: dict[str, Any] | None = None) -> Findings:
    read, login, layer = _reader(sandbox, http)
    await login.token()  # a refused login stops here, with Ramp's status
    raw: list[tuple[str, dict[str, Any]]] = []
    if vendor_id is None:
        listed = (await layer._request("GET", f"{API}/vendors", params={"page_size": 5})).get("data") or []
        if not listed:
            raise RampError("Ramp lists no vendors in this sandbox business")
        vendor_id = listed[0]["id"]
        raw.append(("Vendor", listed[0]))
    f = Findings(vendor_id=vendor_id, login_scopes=read.scopes)
    raw.append(("ApiVendorDetailResource", await layer._request("GET", f"{API}/vendors/{vendor_id}")))
    accounts = await layer._all_pages(f"/vendors/{vendor_id}/accounts", {"page_size": 100})
    bills = await layer._all_pages("/bills", {"vendor_id": vendor_id, "page_size": 100})
    events = await layer._all_pages("/audit-logs/events", {"object_id": vendor_id, "resource_name": VENDOR_RESOURCE,
                                                           "page_size": 100})
    raw += [("ApiVendorBankAccountResource", a) for a in accounts] + [("Bill", b) for b in bills]
    raw += [("AuditLogEventResource", e) for e in events]

    # The core's view, through the layer. A rejection is reported, and the probe goes on.
    for attr, call in (("vendor", layer.get_vendor), ("accounts", layer.list_vendor_accounts), ("bills", layer.list_bills_for_vendor)):
        try:
            setattr(f, attr, await call(vendor_id))
        except RampError as exc:
            f.layer_errors.append(f"{attr}: {exc}")

    # Q202 and Q203: the hold, as the worker's read-back would see it.
    f.hold_events = sorted((e.get("event_time", ""), e["event_type"]) for e in events if e.get("event_type") in (PLACED, RELEASED))
    f.hold_id = f.vendor.hold_id if f.vendor else None
    summaries = {b["id"]: b.get("status_summary") for b in bills}
    unpaid = [b for b in f.bills if not b.paid]
    f.unpaid_bills = [(b.id, summaries.get(b.id), b.payment_date.isoformat() if b.payment_date else None, b.is_on_hold)
                      for b in unpaid]
    f.readback_would_pass = bool(f.hold_id) and all(b.is_on_hold for b in unpaid)

    # Q204: bank-change events, and whether any names one of the vendor's accounts.
    account_ids = {a["id"] for a in accounts}
    for e in events:
        if "bank account" in str(e.get("event_type", "")).lower():
            details = e.get("event_details") if isinstance(e.get("event_details"), dict) else {}
            text = json.dumps(details)
            f.bank_events.append((e["id"], e["event_type"], sorted(details), any(f'"{a}"' in text for a in account_ids)))

    if doc is None:
        f.doc_note = "Ramp's document: not compared (skipped, or it couldn't be read)"
    else:
        f.doc_note = "Ramp's document: compared, top-level fields only"
        f.doc_differences = sorted(set().union(*(_differences(name, obj, doc) for name, obj in raw)))
    return f


@dataclasses.dataclass(frozen=True)
class VendorRow:
    id: str
    name: str
    unpaid_bills: int = 0
    active_accounts: int = 0


async def list_vendors(sandbox: RampSandbox, http: httpx.AsyncClient, *, limit: int = 20) -> list[VendorRow]:
    """D6: vendors with unpaid bills, most first (ties by id), each with its active bank accounts. One
    read of the business's bills, then one accounts read per vendor shown."""
    _, _, layer = _reader(sandbox, http)
    unpaid: dict[str, int] = {}
    names: dict[str, str] = {}
    for b in await layer._all_pages("/bills", {"page_size": 100}):
        vendor = b.get("vendor") or {}
        if vendor.get("id") and b.get("status") != "PAID":  # paid only when Ramp says PAID, as the layer reads it
            unpaid[vendor["id"]] = unpaid.get(vendor["id"], 0) + 1
            names[vendor["id"]] = vendor.get("name") or ""
    rows = []
    for vendor_id in sorted(unpaid, key=lambda v: (-unpaid[v], v))[:limit]:
        accounts = await layer._all_pages(f"/vendors/{vendor_id}/accounts", {"page_size": 100})
        rows.append(VendorRow(vendor_id, names[vendor_id], unpaid[vendor_id],
                              sum(1 for a in accounts if a.get("status") == "active")))
    return rows


def render_list(rows: list[VendorRow]) -> str:
    if not rows:
        return "No vendor in this sandbox business has an unpaid bill."
    lines = ["Vendors with unpaid bills, most first. Read one with:  --vendor <id>", ""]
    lines += [f"  {r.id}  {r.name}  (unpaid bills: {r.unpaid_bills}, active bank accounts: {r.active_accounts})"
              for r in rows]
    return "\n".join(lines)


def render(f: Findings) -> str:
    lines = [f"Logged in to Ramp's sandbox with read scopes only: {' '.join(f.login_scopes)}",
             f"Vendor {f.vendor_id}: {f.vendor.name if f.vendor else '(the layer could not read it)'}", ""]
    lines.append("What the core would see")
    if f.vendor:
        lines.append(f"  vendor: status {f.vendor.status}, updated {f.vendor.updated_at}")
    lines += [f"  account {a.id}: last four {a.last4}, {'active' if a.is_active else 'not active'}" for a in f.accounts]
    lines += [f"  bill {b.id}: {b.status}, payment date {b.payment_date}, {'paid' if b.paid else 'unpaid'}" for b in f.bills]
    lines += [f"  REJECTED by the layer: {e}" for e in f.layer_errors]
    lines += ["", "Q202  Hold events Ramp files under this vendor's id (oldest first)"]
    lines += [f"  {when}  {kind}" for when, kind in f.hold_events] or ["  none"]
    lines.append(f"  the layer reads the hold as: {'ON (' + f.hold_id + ')' if f.hold_id else 'off'}")
    lines += ["", "Q203  Unpaid bills: Ramp's status_summary, payment date, held by the layer's reading"]
    lines += [f"  {bid}  {summary}  {day}  {'held' if held else 'NOT held'}" for bid, summary, day, held in f.unpaid_bills] or ["  none"]
    lines.append(f"  the worker's read-back would {'pass' if f.readback_would_pass else 'fail'}")
    lines += ["", "Q204  Bank-change events under this vendor's id"]
    lines += [f"  {eid}  {kind}  details: {', '.join(keys) or '(none)'}  names one of the vendor's accounts: {'yes' if names else 'no'}"
              for eid, kind, keys, names in f.bank_events] or ["  none"]
    lines += ["", f.doc_note]
    lines += [f"  {d}" for d in f.doc_differences] or (["  no differences"] if "compared" in f.doc_note else [])
    return "\n".join(lines)


def _load_doc(path: str | None) -> dict[str, Any] | None:
    try:
        if path or DOC_PATH.exists():
            return json.loads(Path(path or DOC_PATH).read_text(encoding="utf-8"))
        return httpx.get(DOC_URL, timeout=30.0, follow_redirects=True).json()
    except (OSError, ValueError, httpx.HTTPError):
        return None


def run(argv: list[str] | None = None, environ: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dialback_core.ramp.probe", description=__doc__.split("\n\n")[0])
    parser.add_argument("--vendor", help="the Ramp vendor id to read (default: the first one Ramp lists)")
    parser.add_argument("--spec", help="Ramp's OpenAPI document (default: the repo's copy, else docs.ramp.com)")
    parser.add_argument("--skip-doc", action="store_true", help="don't compare with Ramp's document")
    parser.add_argument("--list", action="store_true", help="list vendors with unpaid bills and their ids, then stop")
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
    doc = None if args.skip_doc or args.list else _load_doc(args.spec)

    async def go() -> str:
        async with read_only_client(transport) as http:
            if args.list:
                return render_list(await list_vendors(sandbox, http))
            return render(await probe(sandbox, http, vendor_id=args.vendor, doc=doc))

    try:
        print(asyncio.run(go()))
    except RampError as exc:
        print(f"Ramp refused or couldn't be reached: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
