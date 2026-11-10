"""Ramp's sandbox in Ramp's real shapes, turned into the core's objects (spec 0097).

Only sandbox datasets reach this (spec 0096 routes them); the mock adapter, and so the demo, the lab
and the pipeline, never run it. Where Ramp's behavior isn't documented, each reading fails closed:
a wrong one sends a case to a person, never a payment to a new account (R1, R7).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .http import API, RampHttpAdapter
from .port import RampAccount, RampAuditEvent, RampBill, RampError, RampHold, RampVendor

PAGE = 100  # Ramp's largest page
PLACED, RELEASED = "Vendor placed on hold", "Vendor hold released"
RAMP_EVENT_NAMES = {"VENDOR_BANK_ACCOUNT_UPDATED": "Bill pay bank account updated"}  # the core's name: Ramp's (D4)
FOUR_DIGITS = re.compile(r"^[0-9]{4}$")
OLDEST = dt.datetime.min.replace(tzinfo=dt.UTC)


def _ts(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def vendor_from(j: dict[str, Any], hold_id: str | None = None) -> RampVendor:
    return RampVendor(id=j["id"], name=j["name"], status="ACTIVE" if j.get("is_active") else "INACTIVE",
                      updated_at=_ts(j.get("updated_at")), hold_id=hold_id)


def account_from(j: dict[str, Any], vendor_id: str) -> RampAccount:
    """Ramp never returns a routing number, so it stays empty; the last four are never invented."""
    last4 = j.get("account_number_last_four")
    if not isinstance(last4, str) or not FOUR_DIGITS.match(last4):
        raise RampError(f"Ramp's account {j.get('id')} has no four last digits, so a change to it can't be checked")
    return RampAccount(id=j["id"], vendor_id=vendor_id, routing_number="", last4=last4, account_type=None,
                       bank_name=None, is_active=j.get("status") == "active", created_at=None)


def bill_from(j: dict[str, Any]) -> RampBill:
    """D3: held when Ramp says ON_HOLD, or unpaid with no payment date (a vendor hold unschedules them)."""
    when = _ts((j.get("payment") or {}).get("payment_date"))
    day = when.date() if when else None
    # Only Ramp's PAID: the read-back skips paid bills, so a bill must never look paid before it is.
    paid = j.get("status") == "PAID"
    held = j.get("status_summary") == "ON_HOLD" or (not paid and day is None)
    return RampBill(id=j["id"], vendor_id=(j.get("vendor") or {}).get("id"), payment_date=day, status=j.get("status"),
                    is_on_hold=held, paid=paid)


def audit_from(j: dict[str, Any], event_type: str) -> RampAuditEvent:
    details = j.get("event_details")
    return RampAuditEvent(id=j["id"], event_type=event_type, actor_id=j.get("actor_id"),
                          target_id=(j.get("primary_reference") or {}).get("id"), occurred_at=_ts(j.get("event_time")),
                          payload=details if isinstance(details, dict) else {})


class RampSandboxAdapter(RampHttpAdapter):
    async def get_vendor(self, vendor_id: str) -> RampVendor:
        return vendor_from(await self._request("GET", f"{API}/vendors/{vendor_id}"), await self._hold_id(vendor_id))

    async def _hold_id(self, vendor_id: str) -> str | None:
        """D3: Ramp's vendor has no hold field, so the newest hold or release event for it says."""
        params = {"object_id": vendor_id, "event_types": f"{PLACED},{RELEASED}", "page_size": PAGE}
        events = await self._all_pages("/audit-logs/events", params)
        if not events:
            return None
        newest = max(events, key=lambda e: _ts(e.get("event_time")) or OLDEST)
        return newest["id"] if newest.get("event_type") == PLACED else None

    async def list_vendors_updated_since(self, since: dt.datetime | None) -> list[RampVendor]:
        params = {"page_size": PAGE} | ({"from_updated_at": since.isoformat()} if since else {})
        return [vendor_from(v) for v in await self._all_pages("/vendors", params)]

    async def list_vendor_accounts(self, vendor_id: str) -> list[RampAccount]:
        return [account_from(a, vendor_id) for a in await self._all_pages(f"/vendors/{vendor_id}/accounts", {"page_size": PAGE})]

    async def list_bills_for_vendor(self, vendor_id: str) -> list[RampBill]:
        bills = [bill_from(b) for b in await self._all_pages("/bills", {"vendor_id": vendor_id, "page_size": PAGE})]
        return [b for b in bills if b.vendor_id == vendor_id]  # filter in code too, in case Ramp ignores vendor_id

    async def place_vendor_hold(self, vendor_id: str, reason: str) -> RampHold:
        await self._request("POST", f"{API}/vendors/{vendor_id}/hold", json={"reason": reason}, empty_ok=True)
        return RampHold(id="", vendor_id=vendor_id, placed_at=None)  # Ramp answers 202 or 204, with no hold id

    async def release_vendor_hold(self, vendor_id: str) -> None:
        await self._request("POST", f"{API}/vendors/{vendor_id}/release", empty_ok=True)

    async def set_bill_payment_date(self, bill_id: str, payment_date: dt.date | None) -> RampBill:
        body = {"payment_date": payment_date.isoformat() if payment_date else None}
        return bill_from(await self._request("PATCH", f"{API}/bills/{bill_id}", json=body))

    async def get_audit_events(self, target_id: str, event_type: str | None = None) -> list[RampAuditEvent]:
        params: dict[str, Any] = {"object_id": target_id, "page_size": PAGE}
        ramp_name = None
        if event_type is not None:
            ramp_name = RAMP_EVENT_NAMES.get(event_type)
            if ramp_name is None:
                raise RampError(f"no Ramp event is known to match {event_type}")
            params["event_types"] = ramp_name
        events = await self._all_pages("/audit-logs/events", params)
        return [audit_from(e, event_type if ramp_name and e.get("event_type") == ramp_name else e.get("event_type", ""))
                for e in events]
