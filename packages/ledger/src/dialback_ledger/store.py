"""The ledger in the database (spec 0024).

`write_ingest`, `add_vendor_domain` and `mark_change_request` write what ingestion found. The core
calls `select_number` and `change_context` by name (apps/core/src/dialback_core/ports.py). Both
read the vendor's ledger rows recorded before the case opened, run the pure rule (`select.py`)
and, for `select_number`, store the candidates and the selection.

Connection (Q45, for Abhi to confirm): a short-lived connection from `DATABASE_URL`, acting as
`service_role` exactly as the core's `system_tx` does. Any error, a missing database included, is
a refusal with a reason: never a pick, and never an exception into the core (R7).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
from email.utils import parseaddr
from typing import Any
from uuid import UUID, uuid5

import phonenumbers as pn
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .ingest import IngestResult
from .select import RULE_VERSION, ChangeRequest, DocumentRef, Sighting, change_context_documents, select

log = logging.getLogger(__name__)
NAMESPACE = UUID("3b8e1f6a-2c4d-4e7f-9a1b-5c6d7e8f9a0b")


def stored_id(dataset_id: UUID, source_id: str | UUID) -> UUID:
    """A document's id in one dataset. The same source file can be loaded into several datasets
    (every demo reset makes a new one), so the stored id is derived from both."""
    return uuid5(NAMESPACE, f"{dataset_id}:{source_id}")


# Writing what ingestion found -------------------------------------------------------------------

async def write_ingest(conn: psycopg.AsyncConnection, *, dataset_id: UUID, vendor_id: UUID, result: IngestResult) -> int:
    """Store an `ingest_all()` result for one vendor, inside the caller's transaction. A document
    already stored is skipped with its observations, so loading twice adds nothing."""
    await conn.execute("set local role service_role")
    stored: set[str] = set()
    docs = sorted(result.documents, key=lambda d: d["parent_document_id"] is not None)  # emails before attachments
    sid = lambda x: stored_id(dataset_id, x) if x else None
    # Skip documents already stored, before inserting: the custody triggers run before any
    # ON CONFLICT, so a re-load at another time would trip them.
    have = {str(r["id"] if isinstance(r, dict) else r[0]) for r in await (await conn.execute(
        "select id from ledger.documents where id = any(%s)", ([sid(d["id"]) for d in docs],))).fetchall()}
    docs = [d for d in docs if str(sid(d["id"])) not in have]
    for d in docs:
        row = await (await conn.execute(
            """insert into ledger.documents (id, dataset_id, vendor_id, kind, sha256, custody_channel, received_at,
                                             claimed_date, sender, thread_id, subject, parent_document_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (id) do nothing returning id""",
            (sid(d["id"]), dataset_id, vendor_id, d["kind"], d["sha256"], d["custody_channel"], d["received_at"],
             d["claimed_date"], d["sender"], d["thread_id"], d["subject"], sid(d["parent_document_id"])))).fetchone()
        if row:
            stored.add(d["id"])
    count = 0
    for o in result.observations:
        if o["document_id"] not in stored:
            continue
        await conn.execute(
            """insert into ledger.observations (dataset_id, document_id, vendor_id, kind, value_normalized, role, party,
                                                quote, visibility, extractor_version)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (dataset_id, sid(o["document_id"]), vendor_id, o["kind"], o["value_normalized"], o["role"], o["party"],
             o["quote"], o["visibility"], o["extractor_version"]))
        count += 1
    return count


async def add_vendor_domain(conn: psycopg.AsyncConnection, *, dataset_id: UUID, vendor_id: UUID, domain: str) -> None:
    await conn.execute("set local role service_role")
    await conn.execute("insert into ledger.vendor_domains (dataset_id, vendor_id, domain) values (%s, %s, %s) "
                       "on conflict do nothing", (dataset_id, vendor_id, domain.strip().lower()))


async def mark_change_request(conn: psycopg.AsyncConnection, *, dataset_id: UUID, vendor_id: UUID,
                              document_id: str | UUID) -> None:
    """Mark an ingested document (by its source id, as `write_ingest` was given it) as the
    vendor's change request."""
    await conn.execute("set local role service_role")
    await conn.execute("insert into ledger.change_requests (dataset_id, vendor_id, document_id) values (%s, %s, %s)",
                       (dataset_id, vendor_id, stored_id(dataset_id, document_id)))


# Reading the vendor's ledger, as of the case ---------------------------------------------------------

async def _inputs(conn: psycopg.AsyncConnection, *, case_id: UUID, dataset_id: UUID, vendor_id: UUID,
                  cutoff: dt.datetime) -> dict[str, Any] | None:
    case = await (await conn.execute("select opened_at from app.cases where id = %s and dataset_id = %s and vendor_id = %s",
                                      (case_id, dataset_id, vendor_id))).fetchone()
    if case is None:
        return None
    opened = case["opened_at"]
    docs = await (await conn.execute(
        "select id, custody_channel, received_at, sender, thread_id, subject, recorded_at from ledger.documents "
        "where dataset_id = %s and vendor_id = %s", (dataset_id, vendor_id))).fetchall()
    rows = await (await conn.execute(
        "select value_normalized, document_id, source_type, seen_at, visibility, role, party, recorded_at from ledger.observations "
        "where dataset_id = %s and vendor_id = %s and kind = 'phone'", (dataset_id, vendor_id))).fetchall()
    domains = await (await conn.execute(
        "select domain from ledger.vendor_domains where dataset_id = %s and vendor_id = %s and recorded_at < %s",
        (dataset_id, vendor_id, opened))).fetchall()
    req = await (await conn.execute(
        """select d.id, d.received_at, d.thread_id, d.subject, d.sender from ledger.change_requests r
             join ledger.documents d on d.id = r.document_id
            where r.dataset_id = %s and r.vendor_id = %s and r.recorded_at < %s
            order by r.recorded_at desc, r.id desc limit 1""", (dataset_id, vendor_id, opened))).fetchone()
    return {
        "case_id": case_id, "dataset_id": dataset_id, "vendor_id": vendor_id, "requested_cutoff": cutoff,
        "case_opened_at": opened, "vendor_domains": tuple(r["domain"] for r in domains),
        "request": ChangeRequest(str(req["id"]), req["received_at"], req["thread_id"], req["subject"], req["sender"]) if req else None,
        "documents": [DocumentRef(str(d["id"]), d["custody_channel"], d["received_at"], d["sender"], d["thread_id"],
                                  d["subject"], d["recorded_at"]) for d in docs],
        "sightings": [Sighting(r["value_normalized"], str(r["document_id"]), r["source_type"], r["seen_at"], r["visibility"],
                               r["role"], r["party"], r["recorded_at"]) for r in rows],
    }


async def _store(conn: psycopg.AsyncConnection, doc: dict) -> None:
    for c in doc["candidates"]:
        row = await (await conn.execute(
            """insert into ledger.contact_points (id, dataset_id, vendor_id, e164, first_seen_at, source_types, status, exclusion_reason)
               values (%s, %s, %s, %s, %s, %s::ledger.source_type[], %s, %s)
               on conflict (dataset_id, vendor_id, e164) do update
                 set first_seen_at = excluded.first_seen_at, source_types = excluded.source_types,
                     status = excluded.status, exclusion_reason = excluded.exclusion_reason
               returning id""",
            (c["contact_point_id"], doc["dataset_id"], doc["vendor_id"], c["e164"], c["first_seen_at"], c["source_types"],
             "eligible" if c["eligible"] else "excluded", ", ".join(c["reasons"]) or None))).fetchone()
        if str(row["id"]) != c["contact_point_id"]:
            raise RuntimeError(f"contact point {c['e164']} is stored under another id")
    await conn.execute(
        """insert into ledger.number_selections (id, dataset_id, case_id, vendor_id, contact_point_id, outcome, rule_version,
                                                 inputs_sha256, selection)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (id) do nothing""",
        (doc["number_selection_id"], doc["dataset_id"], doc["case_id"], doc["vendor_id"], doc.get("contact_point_id"),
         doc["outcome"], doc["rule_version"], doc["inputs_sha256"], Jsonb(doc)))


def _url(database_url: str | None) -> str | None:
    return database_url or os.environ.get("DATABASE_URL") or None


def _refused(reason: str) -> dict:
    return {"rule_version": RULE_VERSION, "reason": reason}


# What the core calls --------------------------------------------------------------------------------

async def select_number(*, case_id: UUID, dataset_id: UUID, vendor_id: UUID, cutoff: dt.datetime,
                        database_url: str | None = None) -> dict:
    """Select and store the number this case will call, or a refusal; never raises (R7)."""
    url = _url(database_url)
    if url is None:
        return _refused("The Trust Ledger has no database (DATABASE_URL is unset), so no number is provable.")
    try:
        async with await psycopg.AsyncConnection.connect(url, row_factory=dict_row) as conn, conn.transaction():
            await conn.execute("set local role service_role")
            inputs = await _inputs(conn, case_id=case_id, dataset_id=dataset_id, vendor_id=vendor_id, cutoff=cutoff)
            if inputs is None:
                return _refused("The Trust Ledger can't find this case for this vendor, so no number is provable.")
            doc = select(**inputs)
            await _store(conn, doc)
    except Exception as exc:
        log.exception("select_number failed for case %s", case_id)
        return _refused(f"The Trust Ledger couldn't make a selection ({type(exc).__name__}), so no number is provable.")
    return {"number_selection_id": doc["number_selection_id"], "rule_version": doc["rule_version"],
            "contact_point_id": doc.get("contact_point_id"), "reason": doc.get("reason"),
            "source_types": doc.get("source_types", [])}


def _e164(text: str) -> str | None:
    try:
        n = pn.parse(text, "US")
    except pn.NumberParseException:
        return None
    return pn.format_number(n, pn.PhoneNumberFormat.E164) if pn.is_valid_number(n) else None


async def change_context(*, case_id: UUID, dataset_id: UUID, vendor_id: UUID, cutoff: dt.datetime,
                         speaker_names: tuple[str, ...], offered_numbers: tuple[str, ...],
                         database_url: str | None = None) -> dict[str, bool]:
    """What the ledger knows about the change request (spec 0005, D5), only the keys it can answer:
    `offered_number_from_request` from where the number was first seen, and `speaker_in_change_thread`
    from the change context's senders (see `_speaker_in`). `speaker_known_before_request` stays
    unknown. Any error answers nothing, which the core reads as unknown."""
    url = _url(database_url)
    if url is None:
        return {}
    try:
        async with await psycopg.AsyncConnection.connect(url, row_factory=dict_row) as conn, conn.transaction():
            await conn.execute("set local role service_role")
            inputs = await _inputs(conn, case_id=case_id, dataset_id=dataset_id, vendor_id=vendor_id, cutoff=cutoff)
        if inputs is None or inputs["request"] is None:
            return {}
        by_number = {c["e164"]: c for c in select(**inputs)["candidates"]}
        context = change_context_documents(**inputs)
    except Exception:
        log.exception("change_context failed for case %s", case_id)
        return {}
    known: dict[str, bool] = {}
    speaker = _speaker_in(context, speaker_names)
    if speaker is not None:
        known["speaker_in_change_thread"] = speaker
    answers = []
    for raw in offered_numbers:
        c = by_number.get(_e164(raw) or "")
        if c is None or "hidden_text_only" in c["reasons"]:
            answers.append(None)                     # the ledger never saw it where a person could
        else:
            answers.append("first_seen_in_change_context" in c["reasons"])
    if any(a is True for a in answers):
        known["offered_number_from_request"] = True
    elif answers and all(a is False for a in answers):
        known["offered_number_from_request"] = False
    return known


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.casefold()))


def _speaker_in(context: list[DocumentRef], speaker_names: tuple[str, ...]) -> bool | None:
    """Is the person on the call someone who wrote in the change context (the request, its thread,
    a lookalike)? True if every word of a declared name appears in a sender's display name or
    address; False only if every sender there has a display name and none matches; otherwise
    unknown, because an unnamed sender could be anyone (R7)."""
    speakers = [t for t in (_tokens(n) for n in speaker_names) if t]
    if not speakers or not context:
        return None
    unnamed = False
    for d in context:
        name, address = parseaddr(d.sender or "")
        if not name.strip():
            unnamed = True
        sender = _tokens(name) | _tokens(address.split("@")[0])
        if any(s <= sender for s in speakers):
            return True
    return None if unnamed else False
