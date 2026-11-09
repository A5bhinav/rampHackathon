"""The vendor risk list (spec 0026): is each vendor's number on file one the rule can prove?

The status is decided by code from `ledger-select-v1`'s candidates, run as of now with no change
request: nothing counts against a number except what arrived after now. "Number on file" is what
the old vendor master says (`vendor_master` sightings), which the rule shows but never counts.
"""
from __future__ import annotations

import datetime as dt
from uuid import UUID

import psycopg

from .select import ChangeRequest, near_miss, select
from .store import vendor_rows

ORDER = ("near_miss", "unproven", "missing", "provable")  # worst first
STATUS_TEXT = {
    "near_miss": "Not provable, and a warning: the number on file is one digit off, or two swapped digits away from, "
                 "a number the Trust Ledger can prove for this vendor.",
    "unproven": "Not provable: the Trust Ledger can't prove the number on file (too few independent sources, too "
                "recent, or seen only where a person couldn't see it).",
    "missing": "No number on file: the vendor master has no phone number for this vendor.",
    "provable": "Provable: the number on file meets every part of the rule.",
}
_NIL = UUID(int=0)


def _status(candidates: list[dict], pick: str | None) -> tuple[str, str | None]:
    on_file = [c for c in candidates if c["on_file"]]
    if not on_file:
        return "missing", None
    provable = {c["e164"] for c in candidates if c["eligible"]}
    for c in on_file:
        if c["eligible"]:
            return "provable", c["e164"]
    for c in on_file:
        if any(near_miss(c["e164"], p) for p in provable):
            return "near_miss", c["e164"]
    return "unproven", on_file[0]["e164"]


async def risk_list(conn: psycopg.AsyncConnection, dataset_id: UUID, *, now: dt.datetime | None = None) -> list[dict]:
    """One row per vendor with ledger documents, worst first, then by name. Reads with the
    caller's connection (dict rows), so a person's token is enough."""
    now = now or dt.datetime.now(dt.UTC)
    vendors = await (await conn.execute(
        """select v.id, v.legal_name from app.vendors v
            where v.dataset_id = %s and exists (select 1 from ledger.documents d where d.dataset_id = v.dataset_id and d.vendor_id = v.id)""",
        (dataset_id,))).fetchall()
    out = []
    for v in vendors:
        rows = await vendor_rows(conn, dataset_id=dataset_id, vendor_id=v["id"], before=now)
        doc = select(case_id=_NIL, dataset_id=dataset_id, vendor_id=v["id"], requested_cutoff=now, case_opened_at=now,
                     request=ChangeRequest("", now, None, None, None), **rows)
        status, on_file = _status(doc["candidates"], doc.get("e164"))
        out.append({"vendor_id": str(v["id"]), "legal_name": v["legal_name"], "status": status, "number_on_file": on_file,
                    "provable_number": doc.get("e164"), "status_text": STATUS_TEXT[status]})
    return sorted(out, key=lambda r: (ORDER.index(r["status"]), r["legal_name"]))
