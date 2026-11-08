"""How a document reached us (ADR-13). Provenance comes from here, never from a model."""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

import rfc8785

CHANNELS = {"email", "contract", "vendor_portal", "listing", "paper_mail", "vendor_master"}


@dataclass(frozen=True)
class Custody:
    document_id: str
    channel: str
    received_at: dt.datetime   # our clock, never a printed date or a Date: header
    sender: str | None
    thread_id: str | None
    subject: str | None
    parent_document_id: str | None


def _when(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError(f"receive time {s!r} has no time zone")
    return t.astimezone(dt.UTC)


def custody_for(document: dict, parent: Custody | None = None) -> Custody:
    """The document's custody. An attachment takes its email's channel and receive time."""
    c = document["custody"]
    if c["channel"] not in CHANNELS:
        raise ValueError(f"unknown custody channel {c['channel']!r}")
    own = Custody(document["id"], c["channel"], _when(c["received_at"]), c.get("sender"), c.get("thread_id"),
                  c.get("subject"), c.get("parent_document_id"))
    if own.parent_document_id is None:
        return own
    if parent is None or parent.document_id != own.parent_document_id:
        raise ValueError(f"attachment {own.document_id} needs its parent {own.parent_document_id}'s custody")
    return Custody(own.document_id, parent.channel, parent.received_at, parent.sender, parent.thread_id,
                   parent.subject, parent.document_id)


def document_sha256(document: dict) -> str:
    """SHA-256 of the document's contents (its pages), not its custody."""
    return hashlib.sha256(rfc8785.dumps(document["pages"])).hexdigest()
