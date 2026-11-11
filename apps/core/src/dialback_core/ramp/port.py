"""The Ramp port: what the core needs from Ramp, in the core's own types.

Field names on the wire are mapped in http.py; nothing else in the core sees Ramp's JSON.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Protocol


class RampError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class RampNotFound(RampError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status=404, retryable=False)


@dataclass(frozen=True)
class RampVendor:
    id: str
    name: str
    status: str | None
    updated_at: dt.datetime | None
    hold_id: str | None          # the active vendor hold, if any (field name to confirm, spec 0002 Q4)


@dataclass(frozen=True)
class RampAccount:
    id: str
    vendor_id: str
    routing_number: str
    last4: str
    account_type: str | None
    bank_name: str | None
    is_active: bool
    created_at: dt.datetime | None
    pending: bool = False        # Ramp's sandbox: a change waiting for review (spec 0093); the mock never sets it


@dataclass(frozen=True)
class RampBill:
    id: str
    vendor_id: str
    payment_date: dt.date | None
    status: str | None
    is_on_hold: bool
    paid: bool


@dataclass(frozen=True)
class RampHold:
    id: str
    vendor_id: str
    placed_at: dt.datetime | None


@dataclass(frozen=True)
class RampAuditEvent:
    id: str
    event_type: str
    actor_id: str | None
    target_id: str | None
    occurred_at: dt.datetime | None
    payload: dict[str, Any] = field(default_factory=dict)


class RampPort(Protocol):
    async def get_vendor(self, vendor_id: str) -> RampVendor: ...
    async def list_vendors_updated_since(self, since: dt.datetime | None) -> list[RampVendor]: ...
    async def list_vendor_accounts(self, vendor_id: str) -> list[RampAccount]: ...
    async def list_bills_for_vendor(self, vendor_id: str) -> list[RampBill]: ...
    async def place_vendor_hold(self, vendor_id: str, reason: str) -> RampHold: ...
    async def release_vendor_hold(self, vendor_id: str) -> None: ...
    async def set_bill_payment_date(self, bill_id: str, payment_date: dt.date | None) -> RampBill: ...
    async def get_audit_events(self, target_id: str, event_type: str | None = None) -> list[RampAuditEvent]: ...
