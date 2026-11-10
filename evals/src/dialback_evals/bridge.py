"""Spec 0052, decision 4: the one bridge from ingestion to selection.

`world_from_documents` runs a set of page-model documents through the ledger's real `ingest_all`, with
the reader the caller names (never one taken from the environment; spec 0049, D8), and turns what
ingestion produced into `select()`'s inputs: the change request (found by its document id), the
vendor's domains, the times, and every document and sighting exactly as ingestion recorded them. The
lab never edits `packages/ledger`; this only reads its output.
"""
from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable

from dialback_ledger.ingest import ingest_all
from dialback_ledger.reader import Reader
from dialback_ledger.select import ChangeRequest, DocumentRef, Settings, Sighting

from .worldgen import World

CASE, DATASET, VENDOR = (uuid.UUID(int=n) for n in (1, 2, 3))


def world_from_documents(documents: list[dict], *, request_id: str | None, vendor_domains: Iterable[str],
                         requested_cutoff: dt.datetime, case_opened_at: dt.datetime, reader: Reader,
                         extractor_version: str, poison: frozenset[str] = frozenset(),
                         settings: Settings | None = None) -> World:
    """The select() world real ingestion makes of `documents`. `request_id` names the change request's
    document (None: no request is in the ledger). Every document is recorded when it was received, and
    every sighting when its document was."""
    if reader is None:
        raise ValueError("name the reader: the bridge never takes one from the environment (spec 0049, D8)")
    ingested = ingest_all(documents, extractor_version=extractor_version, reader=reader)
    request = None
    if request_id is not None:
        req = next((d for d in ingested.documents if d["id"] == request_id), None)
        if req is None:
            raise ValueError(f"the change request {request_id!r} is not among the documents")
        request = ChangeRequest(req["id"], req["received_at"], req["thread_id"], req["subject"], req["sender"])
    chosen = settings if settings is not None else Settings()
    kwargs = {
        "case_id": CASE, "dataset_id": DATASET, "vendor_id": VENDOR,
        "vendor_domains": tuple(vendor_domains),
        "request": request,
        "requested_cutoff": requested_cutoff, "case_opened_at": case_opened_at,
        "documents": [DocumentRef(d["id"], d["custody_channel"], d["received_at"], d["sender"], d["thread_id"],
                                  d["subject"], d["received_at"]) for d in ingested.documents],
        "sightings": [Sighting(o["value_normalized"], o["document_id"], o["source_type"], o["seen_at"], o["visibility"],
                               o["role"], o["party"], o["seen_at"]) for o in ingested.observations],
        "settings": chosen,
    }
    return World(kwargs=kwargs, poison=frozenset(poison), settings=chosen)
