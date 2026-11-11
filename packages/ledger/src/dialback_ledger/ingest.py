"""ingest(): documents in, ledger.observations-shaped rows out (spec 0022).

Code runs every stage but labeling: custody, text, visibility, candidates, then the reader on
visible text only, then grounding. Every candidate code found becomes an observation; a label can
only describe it. Writing the rows is the database wiring (Q45).

A real PDF goes through `ingest_pdf` (spec 0092): read into the page model, then the same `ingest()`.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from .candidates import find_phones
from .custody import Custody, custody_for, document_sha256
from .flags import reader_mode
from .grounding import ground
from .reader import Reader, ReaderFailed, reader_for
from .text import lines, visible_text


@dataclass
class IngestResult:
    documents: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    dropped: list = field(default_factory=list)


def ingest(document: dict, custody: Custody, *, extractor_version: str, reader: Reader | None = None,
           result: IngestResult | None = None, sha256: str | None = None) -> IngestResult:
    """One document's rows. `sha256` is the custody hash when the caller holds the file itself (a PDF's
    bytes, spec 0092 D5); by default it is the hash of the page model's pages."""
    if custody.document_id != document["id"]:
        raise ValueError(f"custody for {custody.document_id} given for document {document['id']}")
    result = result if result is not None else IngestResult()
    reader = reader or reader_for(reader_mode())
    ls = lines(document)
    cands = find_phones(ls)
    try:
        labels = reader.label(visible_text(ls))
    except ReaderFailed as exc:   # fail closed: nothing of this document reaches `result` (spec 0049)
        raise exc.for_document(document["id"]) from None
    accepted, dropped = ground(labels, ls, cands)
    labels = {g.candidate: g for g in accepted}
    result.dropped += [(document["id"], raw, why) for raw, why in dropped]
    result.documents.append({
        "id": document["id"], "kind": document["kind"], "sha256": sha256 or document_sha256(document),
        "custody_channel": custody.channel, "received_at": custody.received_at,
        "claimed_date": document.get("claimed_date"), "sender": custody.sender, "thread_id": custody.thread_id,
        "subject": custody.subject, "parent_document_id": custody.parent_document_id})
    for c in cands:
        g = labels.get(c)
        result.observations.append({
            "document_id": document["id"], "kind": "phone", "value_normalized": c.e164,
            "role": g.role if g else "unknown", "party": g.party if g else "unknown",
            "quote": (g.quote if g else c.raw) if c.visibility == "visible" else None,
            "visibility": c.visibility, "source_type": custody.channel, "seen_at": custody.received_at,
            "extractor_version": extractor_version})
    return result


def ingest_pdf(data: bytes, document: dict, custody: Custody, *, extractor_version: str, reader: Reader | None = None,
               result: IngestResult | None = None) -> IngestResult:
    """A real PDF's rows (spec 0092, D1 and D5). `document` is everything but its pages (any `pages` it has are
    replaced by what the file holds). The file is read first: a refused file (`PdfRefused`) leaves `result`
    untouched. The stored hash is the file's, and `extractor_version` names both the text reader and the
    labeler: `pdf-<reader version>/<labeler version>`."""
    from .pdf import READER_VERSION, PdfRefused, read_pdf  # here, so page-model ingestion never loads PDFium
    try:
        pages = read_pdf(data)
    except PdfRefused as exc:   # fail closed: nothing of this document reaches `result` (D4)
        raise exc.for_document(document["id"]) from None
    return ingest({**document, "pages": pages}, custody, extractor_version=f"pdf-{READER_VERSION}/{extractor_version}",
                  reader=reader, result=result, sha256=hashlib.sha256(data).hexdigest())


def ingest_all(documents: list[dict], *, extractor_version: str, reader: Reader | None = None,
               pdfs: Mapping[str, bytes] | None = None) -> IngestResult:
    """Ingest a batch; an attachment's parent must be in the batch (its custody comes from there). A document
    whose id is in `pdfs` is read from that PDF instead of its pages (spec 0092)."""
    by_id = {d["id"]: d for d in documents}
    custody: dict[str, Custody] = {}

    def resolve(doc_id: str, chain: tuple[str, ...] = ()) -> Custody:
        if doc_id in chain:
            raise ValueError(f"attachment cycle: {' -> '.join((*chain, doc_id))}")
        if doc_id not in custody:
            doc = by_id[doc_id]
            parent = doc["custody"].get("parent_document_id")
            custody[doc_id] = custody_for(doc, resolve(parent, (*chain, doc_id)) if parent in by_id else None)
        return custody[doc_id]

    result = IngestResult()
    reader = reader or reader_for(reader_mode())
    for d in documents:
        if pdfs is not None and d["id"] in pdfs:
            ingest_pdf(pdfs[d["id"]], d, resolve(d["id"]), extractor_version=extractor_version, reader=reader,
                       result=result)
        else:
            ingest(d, resolve(d["id"]), extractor_version=extractor_version, reader=reader, result=result)
    return result
