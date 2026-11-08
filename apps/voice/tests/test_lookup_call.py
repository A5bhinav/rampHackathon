"""lookup_call maps a joined call row to the CallDial the Twilio routes read, and fails closed
(spec 0014, decision 4). The number comes from the Trust Ledger pick and the names from the case
and the pick's vendor; those tables are not final (Q40/Q50), so the psycopg query that builds the
row lands with the ledger. This tests the mapper that both that query and the routes go through:
a complete row becomes a CallDial, and anything short of complete becomes None, so a route never
dials or speaks on a half-known call (R2, R7)."""
from __future__ import annotations

import uuid

from dialback_voice.store import CallDial, dial_from_row

CASE = uuid.UUID("c0ffee00-1111-4222-8333-444455556666")

ROW = {"case_id": CASE, "attempt": 2, "number_e164": "+14155550123",
       "customer_name": "Northwind Traders", "callback_number": "(415) 555-0100",
       "vendor_names": ["Acme Fasteners", "Globex"]}


def test_a_complete_row_becomes_a_call_dial():
    d = dial_from_row(ROW)
    assert d == CallDial(case_id=CASE, attempt=2, number_e164="+14155550123",
                         customer_name="Northwind Traders", callback_number="(415) 555-0100",
                         vendor_names=("Acme Fasteners", "Globex"))


def test_a_missing_row_is_none():
    assert dial_from_row(None) is None


def test_any_missing_required_field_fails_closed():
    # The disclosure needs the customer name and the callback number; the token needs the case,
    # attempt and dialed number. A row short any one of them is not a call we can safely run.
    for field in ("case_id", "attempt", "number_e164", "customer_name", "callback_number"):
        absent = {k: v for k, v in ROW.items() if k != field}
        assert dial_from_row(absent) is None, f"missing {field} should fail closed"
        blank = ROW | {field: None}
        assert dial_from_row(blank) is None, f"null {field} should fail closed"


def test_vendor_names_default_to_empty():
    # No vendor to name is fine (the disclosure does not use them); it is not a fail-closed case.
    d = dial_from_row({k: v for k, v in ROW.items() if k != "vendor_names"})
    assert d is not None and d.vendor_names == ()
