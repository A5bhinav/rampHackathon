"""ledger-select-v1: which number a case will call, or none (spec 0021, design §4).

A pure function: sightings and their documents' custody in, a selection.schema.json document
out. Code decides; no model output reaches here except role and party labels. A number needs
every visible sighting labeled the vendor's, so a label can refuse a number or, for a number that
already meets every rule in code, allow it; it can never make up for a rule in code (property 5).

"Seen" for age, ranking and near misses means a visible sighting before the cutoff, outside the
change context, in a channel that counts (email only from the vendor's own domains). Nothing else
can make a number look older or established: not an old hijacked thread, a lookalike's message,
a line hidden in a PDF, or the old number on file (spec 0021).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

import rfc8785

from .lookalike import is_lookalike, sender_domain

RULE_VERSION = "ledger-select-v1"
NAMESPACE = uuid.UUID("0f4e2b8a-6c1d-4e3f-9a5b-7d2c1e0f3a64")
SOURCE_TYPES = {"email", "contract", "vendor_portal", "listing", "paper_mail"}  # vendor_master never counts
ROLES = {"main", "ar", "billing", "fax", "unknown"}
NO_REQUEST = "The change request isn't in the ledger, so the change context is unknown and no number is provable."
NO_PICK = ("No number was seen in at least {n} independent source types, at least {d} days before the change request "
           "and outside it, without being a near miss of an older number.")


@dataclass(frozen=True)
class DocumentRef:
    id: str
    channel: str
    received_at: dt.datetime
    sender: str | None
    thread_id: str | None
    subject: str | None
    recorded_at: dt.datetime


@dataclass(frozen=True)
class Sighting:
    e164: str
    document_id: str
    channel: str              # informational; the rule reads the document's custody channel
    received_at: dt.datetime  # informational; the rule reads the document's receive time
    visibility: str
    role: str
    party: str
    recorded_at: dt.datetime


@dataclass(frozen=True)
class ChangeRequest:
    document_id: str
    received_at: dt.datetime
    thread_id: str | None
    subject: str | None
    sender: str | None


@dataclass(frozen=True)
class Settings:
    min_source_types: int = 2
    min_age_days: int = 90
    near_miss_distance: int = 1
    same_subject_window_days: int = 30
    established_days: int = 365  # Q43; fixed in v1, not in rule_settings


DEFAULT = Settings()


def near_miss(a: str, b: str, distance: int = 1) -> bool:
    """Up to `distance` digits off, or two adjacent digits swapped."""
    if a == b or len(a) != len(b):
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    swapped = len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    return len(diff) <= distance or swapped


def _utc(t: dt.datetime) -> str:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("times must carry a time zone")
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _subject(s: str | None) -> str:
    s = (s or "").strip().casefold()
    while (m := re.match(r"^((re|fwd?|aw|sv)\s*:|\[[^\]]*\])\s*", s)):  # Re:, Fwd:, [EXTERNAL] ...
        s = s[m.end():]
    return " ".join(s.split())


def _frame(*, documents: Sequence[DocumentRef], request: ChangeRequest | None, requested_cutoff: dt.datetime,
           case_opened_at: dt.datetime, domains: tuple[str, ...], settings: Settings):
    """The documents recorded before the case opened, the cutoff, and the change context."""
    docs: dict[str, DocumentRef] = {}
    conflicted: set[str] = set()  # two different documents under one id: both count against (R7)
    for d in sorted((d for d in documents if d.recorded_at < case_opened_at), key=repr):          # replay
        if d.id in docs and docs[d.id] != d:
            conflicted.add(d.id)
        docs.setdefault(d.id, d)
    look_ids = {d.id for d in docs.values() if (sd := sender_domain(d.sender)) and is_lookalike(sd, domains)}
    starts = [requested_cutoff, *(docs[i].received_at for i in look_ids)] + ([request.received_at] if request else [])
    cutoff = min(starts)
    window = dt.timedelta(days=settings.same_subject_window_days)

    def in_context(d: DocumentRef) -> bool:
        if d.received_at > cutoff or d.id in look_ids or d.id in conflicted:
            return True
        if request is None:
            return True  # unknown change context: everything counts against (R7)
        return ((request.thread_id is not None and d.thread_id == request.thread_id) or d.id == request.document_id
                or (_subject(d.subject) == _subject(request.subject) != "" and cutoff - d.received_at <= window))

    return docs, cutoff, in_context


def change_context_documents(*, vendor_domains: Iterable[str], request: ChangeRequest | None, requested_cutoff: dt.datetime,
                             case_opened_at: dt.datetime, documents: Sequence[DocumentRef],
                             settings: Settings = DEFAULT, **_: object) -> list[DocumentRef]:
    """The documents in the change context, by the same definition `select()` uses."""
    domains = tuple(d.strip().lower().rstrip(".") for d in vendor_domains)
    docs, _cutoff, in_context = _frame(documents=documents, request=request, requested_cutoff=requested_cutoff,
                                       case_opened_at=case_opened_at, domains=domains, settings=settings)
    return [d for d in docs.values() if in_context(d)]


def select(*, case_id: uuid.UUID, dataset_id: uuid.UUID, vendor_id: uuid.UUID, vendor_domains: Iterable[str],
           request: ChangeRequest | None, requested_cutoff: dt.datetime, case_opened_at: dt.datetime,
           documents: Sequence[DocumentRef], sightings: Sequence[Sighting], settings: Settings = DEFAULT) -> dict:
    domains = tuple(d.strip().lower().rstrip(".") for d in vendor_domains)
    docs, cutoff, in_context = _frame(documents=documents, request=request, requested_cutoff=requested_cutoff,
                                      case_opened_at=case_opened_at, domains=domains, settings=settings)
    rows = sorted((s for s in sightings if s.recorded_at < case_opened_at and s.document_id in docs), key=repr)

    def counts(d: DocumentRef) -> bool:
        """A source type that counts: a known channel, and for email the vendor's own domain."""
        if d.channel not in SOURCE_TYPES:
            return False
        sd = sender_domain(d.sender) if d.channel == "email" else None
        return d.channel != "email" or (sd is not None and any(sd == v or sd.endswith("." + v) for v in domains))

    numbers: dict[str, list[tuple[Sighting, DocumentRef, bool]]] = {}
    for s in rows:
        d = docs[s.document_id]
        numbers.setdefault(s.e164, []).append((s, d, in_context(d)))

    facts = {}
    for e164, seen in numbers.items():
        visible = [(s, d, ctx) for s, d, ctx in seen if s.visibility == "visible"]
        outside = [d.received_at for _, d, ctx in visible if not ctx]
        clean = [(s, d) for s, d, ctx in visible if not ctx and counts(d)]  # "seen": what age and rank rest on
        first_clean = min((d.received_at for _, d in clean), default=None)
        first_ctx = min((d.received_at for _, d, ctx in visible if ctx), default=None)
        sources = sorted({d.channel for _, d in clean})
        reasons = set()
        if not visible:
            reasons.add("hidden_text_only")
        elif first_ctx is not None and (not outside or first_ctx <= min(outside)):
            reasons.add("first_seen_in_change_context")
        if visible and any(s.party != "vendor" for s, _, _ in visible):
            reasons.add("not_vendor_party")
        if any(str(s.role).strip().lower() == "fax" for s, _, _ in visible):
            reasons.add("fax_line")
        if len(sources) < settings.min_source_types:
            reasons.add("too_few_source_types")
        if first_clean is None or cutoff - first_clean < dt.timedelta(days=settings.min_age_days):
            reasons.add("younger_than_minimum_age")
        shown = first_clean or min((d.received_at for _, d, _ in visible), default=None) or min(d.received_at for _, d, _ in seen)
        roles = {s.role if s.role in ROLES else "unknown" for s, _, _ in visible}
        ranking_roles = {s.role for s, _ in clean}  # the change context can't reorder numbers
        facts[e164] = {"seen": first_clean, "shown": shown, "sources": sources, "reasons": reasons, "roles": roles,
                       "ranking_roles": ranking_roles,
                       "on_file": any(d.channel == "vendor_master" for _, d, _ in visible), "count": len(seen)}

    established = dt.timedelta(days=settings.established_days)
    for a, fa in facts.items():
        mine = fa["seen"] or fa["shown"]
        for b, fb in facts.items():
            # Older or first seen together: a one-off sibling in the same document flags both.
            if b == a or fb["seen"] is None or fb["seen"] > mine or not near_miss(a, b, settings.near_miss_distance):
                continue
            if fa["seen"] is not None and cutoff - fa["seen"] >= established and cutoff - fb["seen"] >= established:
                continue
            fa["reasons"].add("near_miss_of_older_number")
            fa.setdefault("near_miss_of", b)

    rank = sorted((e for e, f in facts.items() if not f["reasons"]),
                  key=lambda e: (facts[e]["seen"], -len(facts[e]["sources"]), not facts[e]["ranking_roles"] & {"ar", "billing"}, e))
    point = lambda e: str(uuid.uuid5(NAMESPACE, f"{vendor_id}:{e}"))

    candidates = []
    for e in rank + sorted(set(facts) - set(rank)):
        f = facts[e]
        c = {"contact_point_id": point(e), "e164": e, "first_seen_at": _utc(f["shown"]), "source_types": f["sources"],
             "on_file": f["on_file"], "roles": sorted(f["roles"]), "observation_count": f["count"],
             "eligible": e in rank, "reasons": sorted(f["reasons"])}
        if e in rank:
            c["rank"] = rank.index(e) + 1
        if "near_miss_of" in f:
            c["near_miss_of"] = point(f["near_miss_of"])
        candidates.append(c)

    rule_settings = {k: v for k, v in asdict(settings).items() if k != "established_days"}
    inputs = {"rule_version": RULE_VERSION, "settings": asdict(settings), "domains": sorted(domains),
              "request": asdict(request) if request else None, "requested_cutoff": requested_cutoff,
              "documents": sorted((asdict(d) for d in docs.values()), key=lambda d: d["id"]),
              "sightings": [asdict(s) for s in rows]}
    digest = hashlib.sha256(rfc8785.dumps(_jsonable(inputs))).hexdigest()
    out = {"v": 1, "number_selection_id": str(uuid.uuid5(NAMESPACE, f"{case_id}:{RULE_VERSION}:{digest}")),
           "case_id": str(case_id), "dataset_id": str(dataset_id), "vendor_id": str(vendor_id),
           "rule_version": RULE_VERSION, "rule_settings": rule_settings, "requested_cutoff": _utc(requested_cutoff),
           "cutoff": _utc(cutoff), "candidates": candidates, "inputs_sha256": digest}
    if rank:
        pick = rank[0]
        out |= {"outcome": "picked", "contact_point_id": point(pick), "e164": pick, "source_types": facts[pick]["sources"],
                "alternates": [{"contact_point_id": point(e), "e164": e, "source_types": facts[e]["sources"]}
                               for e in rank[1:3]]}
    else:
        out |= {"outcome": "no_provable_number",
                "reason": NO_REQUEST if request is None else NO_PICK.format(n=settings.min_source_types,
                                                                            d=settings.min_age_days)}
    return out


def _jsonable(v):
    if isinstance(v, dt.datetime):
        return _utc(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v
