"""The vendor risk list (spec 0026): is each vendor's number on file one the rule can prove?

The status is decided by code from `ledger-select-v1`'s candidates, run as of now with no change
request: nothing counts against a number except what arrived after now. "Number on file" is what
the old vendor master says (`vendor_master` sightings), which the rule shows but never counts.
"""
from __future__ import annotations

import datetime as dt
from uuid import UUID

import psycopg

from .select import ChangeRequest, _utc, near_miss, select
from .store import vendor_rows

ORDER = ("near_miss", "unproven", "missing", "provable")  # worst first
STATUS_TEXT = {
    "near_miss": "Not provable, and a warning: the number on file is one digit off, or two swapped digits away from, "
                 "a number the Trust Ledger can prove for this vendor.",
    "unproven": "Not provable: the number on file fails at least one part of the rule, such as two independent "
                "sources, months of history, or being the vendor's own line where a person could see it.",
    "missing": "No number on file: the vendor master has no phone number for this vendor.",
    "provable": "Provable: the number on file meets every part of the rule.",
}
_NIL = UUID(int=0)


def _status(candidates: list[dict], pick: str | None) -> tuple[str, str | None]:
    """The worst status among the numbers on file, so a near miss is never hidden behind a
    provable line (spec 0026)."""
    on_file = [c for c in candidates if c["on_file"]]
    if not on_file:
        return "missing", None
    provable = {c["e164"] for c in candidates if c["eligible"]}

    def one(c: dict) -> str:
        if c["eligible"]:
            return "provable"
        return "near_miss" if any(near_miss(c["e164"], p) for p in provable) else "unproven"

    worst = min(on_file, key=lambda c: ORDER.index(one(c)))
    return one(worst), worst["e164"]


async def _as_of_now(conn: psycopg.AsyncConnection, dataset_id: UUID, vendor: dict, now: dt.datetime) -> tuple[dict, dict]:
    """The vendor's risk row and the rule's document, as of `now` with no change request."""
    rows = await vendor_rows(conn, dataset_id=dataset_id, vendor_id=vendor["id"], before=now)
    # A placeholder request that matches nothing: with none at all, the rule would count every
    # document as change context (R7) and nothing would be provable.
    doc = select(case_id=_NIL, dataset_id=dataset_id, vendor_id=vendor["id"], requested_cutoff=now, case_opened_at=now,
                 request=ChangeRequest("", now, None, None, None), **rows)
    status, on_file = _status(doc["candidates"], doc.get("e164"))
    return {"vendor_id": str(vendor["id"]), "legal_name": vendor["legal_name"], "status": status, "number_on_file": on_file,
            "provable_number": doc.get("e164"), "status_text": STATUS_TEXT[status]}, doc


async def risk_list(conn: psycopg.AsyncConnection, dataset_id: UUID, *, now: dt.datetime | None = None) -> list[dict]:
    """One row per vendor with ledger documents, worst first, then by name. Reads with the
    caller's connection (dict rows), so a person's token is enough."""
    now = now or dt.datetime.now(dt.UTC)
    vendors = await (await conn.execute(
        """select v.id, v.legal_name from app.vendors v
            where v.dataset_id = %s and exists (select 1 from ledger.documents d where d.dataset_id = v.dataset_id and d.vendor_id = v.id)""",
        (dataset_id,))).fetchall()
    out = [(await _as_of_now(conn, dataset_id, v, now))[0] for v in vendors]
    return sorted(out, key=lambda r: (ORDER.index(r["status"]), r["legal_name"]))


DOCUMENT_LIMIT = 500
_RULE_KEYS = ("rule_version", "rule_settings", "cutoff", "outcome", "candidates")


async def vendor_ledger(conn: psycopg.AsyncConnection, dataset_id: UUID, vendor_id: UUID, *,
                        now: dt.datetime | None = None) -> dict | None:
    """One vendor's history (spec 0104): its risk row, the rule as of now (the same run as the risk
    list; nothing is stored), and its documents with their phone observations. None when the vendor
    isn't on the dataset as the caller sees it. Bank, email and domain observations are left out
    (ADR-8), and a quote's other long digit runs are redacted on read."""
    from .llm_reader import for_the_model  # the reader's redaction; imported here, as reader.py does
    now = now or dt.datetime.now(dt.UTC)
    vendor = await (await conn.execute("select id, legal_name from app.vendors where id = %s and dataset_id = %s",
                                       (vendor_id, dataset_id))).fetchone()
    if vendor is None:
        return None
    risk, doc = await _as_of_now(conn, dataset_id, vendor, now)
    rule = {k: doc[k] for k in _RULE_KEYS} | {"as_of": doc["requested_cutoff"]}
    if doc["outcome"] == "picked":
        rule |= {"contact_point_id": doc["contact_point_id"], "alternates": doc["alternates"]}
    count = (await (await conn.execute("select count(*) as n from ledger.documents where dataset_id = %s and vendor_id = %s",
                                       (dataset_id, vendor_id))).fetchone())["n"]
    docs = await (await conn.execute(
        "select id, kind::text as kind, custody_channel::text as custody_channel, received_at, claimed_date, sender, subject, "
        "thread_id, parent_document_id from ledger.documents where dataset_id = %s and vendor_id = %s "
        "order by received_at desc, coalesce(parent_document_id, id), parent_document_id is not null, id limit %s",
        (dataset_id, vendor_id, DOCUMENT_LIMIT))).fetchall()
    seen: dict = {d["id"]: [] for d in docs}
    for o in await (await conn.execute(
            "select document_id, value_normalized, role::text as role, party::text as party, visibility::text as visibility, quote "
            "from ledger.observations where document_id = any(%s) and kind = 'phone' order by value_normalized, id",
            (list(seen),))).fetchall():
        seen[o["document_id"]].append({"e164": o["value_normalized"], "role": o["role"], "party": o["party"],
                                       "visibility": o["visibility"],
                                       "quote": None if o["quote"] is None else for_the_model(o["quote"])[0]})
    return {"risk": risk, "rule": rule, "document_count": count, "documents": [{
        "document_id": str(d["id"]), "kind": d["kind"], "custody_channel": d["custody_channel"], "received_at": _utc(d["received_at"]),
        "claimed_date": d["claimed_date"].isoformat() if d["claimed_date"] else None, "sender": d["sender"],
        "subject": d["subject"], "thread_id": d["thread_id"],
        "parent_document_id": str(d["parent_document_id"]) if d["parent_document_id"] else None,
        "observations": seen[d["id"]]} for d in docs]}
