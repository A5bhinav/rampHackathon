"""The real psycopg adapter behind the voice store seam (spec 0027). PR1: the synchronous lookup.

`make_lookup` builds the `lookup_call` the Twilio routes read: given a call_id, one query by that id
joins voice.calls to the case's vendor and the pinned ledger pick, and `dial_from_row` maps the row
to a CallDial, failing closed to None on anything missing (R2, R7). The customer and callback come
from settings, never the callee (R8). Synchronous, because app.py calls the lookup without await
(spec 0027, decision 1); the Twilio-callback volume makes the brief blocking query acceptable. A
short-lived connection per call keeps PR1 thin; a shared pool can come with the async store (PR2)."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .store import CallDial, dial_from_row

# One row per call_id: the case and its vendor, and the number from the pinned contact point (a left
# join, so a call with no pick yields a null number and dial_from_row fails it closed). Reading
# app.cases is a select, never a write, so the money-path "one door" is untouched.
_SQL = """
    select c.case_id, c.attempt, cp.e164 as number_e164, v.legal_name, v.trade_names
    from voice.calls c
    join app.cases ca on ca.id = c.case_id
    join app.vendors v on v.id = ca.vendor_id
    left join ledger.contact_points cp on cp.id = c.contact_point_id
    where c.id = %(call_id)s
"""


def make_lookup(database_url: str, *, customer_name: str,
                callback_number: str) -> Callable[[UUID], CallDial | None]:
    """A synchronous lookup_call backed by psycopg: one query by call_id, mapped through
    dial_from_row, with the fixed disclosure names filled from settings."""
    def lookup_call(call_id: UUID) -> CallDial | None:
        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            row = conn.execute(_SQL, {"call_id": call_id}).fetchone()
        if row is None:
            return None
        names = tuple(n for n in (row["legal_name"], *(row["trade_names"] or ())) if n)
        return dial_from_row({**row, "vendor_names": names,
                              "customer_name": customer_name, "callback_number": callback_number})
    return lookup_call
