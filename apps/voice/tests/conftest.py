"""Shared fixtures for the voice-service tests: a call context and a deterministic clock.

CALLBACK is the string packages/policy's guard tests already treat as a valid callback number,
so the agent speaking it in the disclosure is not counted as an account-digit leak."""
from __future__ import annotations

import datetime as dt

import pytest
from dialback_policy.script import Script
from dialback_policy.step import CallContext

CALLBACK = "(415) 555-0100"


@pytest.fixture
def make_ctx():
    def _make(call_id: str = "6b1e2f0a-4c3d-4e5f-8a9b-0c1d2e3f4a5b",
              customer: str = "Northwind Traders", vendor: str = "Acme Fasteners") -> CallContext:
        return CallContext(call_id=call_id, script=Script(customer, CALLBACK), vendor_names=(vendor,))
    return _make


@pytest.fixture
def clock():
    def _clock(step: dt.timedelta = dt.timedelta(seconds=10),
               start: dt.datetime = dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)):
        box = {"t": start}
        def now() -> dt.datetime:
            cur = box["t"]
            box["t"] = cur + step
            return cur
        return now
    return _clock
