"""Spec 0033, Task 2: the invariant oracle over select() worlds.

`violations(world, out)` checks the rule's guarantees (I1-I7, I9) on one output. It recomputes
visibility, counting channels, the change context, age and near misses from the world's raw inputs:
a reference copy of the rule's frame, so it catches an implementation slip in select() (assembly of
reasons, eligibility, ranking inputs) but not a misunderstanding both copies share. It reuses only
the ledger's lookalike-domain classifier and sender parsing (classification, not the decision).
`metamorphic(world)` re-runs the rule on mutated copies of the world (I8, I10, I11, and "attacker
evidence never helps"). `liveness(world, out)` is the L1 over-blocking floor. Every function returns
violation labels; an empty list means the rule held.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path

from dialback_ledger.lookalike import is_lookalike, sender_domain
from dialback_ledger.select import DocumentRef, Sighting
from jsonschema import Draft202012Validator

from .worldgen import World, _address, run

COUNTING = frozenset({"email", "contract", "vendor_portal", "listing", "paper_mail"})  # not vendor_master
FRESH = "+10895550199"   # fictional, at least 3 digits from every generated number; a probe target
LOOKALIKE_PROBE_DAYS = (30, 100, 400)   # a lookalike email this many days before the request
_SCHEMA = Path(__file__).resolve().parents[3] / "packages/contracts/schemas/selection.schema.json"
_PREFIX = re.compile(r"^((re|fwd?|aw|sv)\s*:|\[[^\]]*\])\s*")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA.read_text()))


def _norm(subject: str | None) -> str:
    s = (subject or "").strip().casefold()
    while m := _PREFIX.match(s):
        s = s[m.end():]
    return " ".join(s.split())


def _near(a: str, b: str, distance: int) -> bool:
    """Up to `distance` digits off, or two adjacent digits swapped, on equal-length numbers."""
    if a == b or len(a) != len(b):
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
    swapped = len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    return len(diff) <= distance or swapped


def _own(sd: str | None, domains: tuple[str, ...]) -> bool:
    return sd is not None and any(sd == v or sd.endswith("." + v) for v in domains)


@dataclasses.dataclass(frozen=True)
class _Number:
    visible: tuple[Sighting, ...]
    visible_counting: tuple[Sighting, ...]
    clean: tuple[Sighting, ...]          # visible, counting, outside the change context
    first_clean: dt.datetime | None
    shown: dt.datetime                   # the time the rule reasons from when nothing is clean
    channels: frozenset[str]


@dataclasses.dataclass(frozen=True)
class _Frame:
    docs: dict[str, DocumentRef]
    cutoff: dt.datetime
    numbers: dict[str, _Number]
    in_context_ids: frozenset[str]


def _frame(world: World) -> _Frame:
    kw, s = world.kwargs, world.settings
    opened, request = kw["case_opened_at"], kw["request"]
    domains = tuple(v.strip().lower().rstrip(".") for v in kw["vendor_domains"])
    docs: dict[str, DocumentRef] = {}
    conflicted: set[str] = set()
    for d in sorted((d for d in kw["documents"] if d.recorded_at < opened), key=repr):
        if d.id in docs and docs[d.id] != d:
            conflicted.add(d.id)
        docs.setdefault(d.id, d)
    look = {d.id for d in docs.values() if (sd := sender_domain(d.sender)) and is_lookalike(sd, domains)}
    starts = [kw["requested_cutoff"], *(docs[i].received_at for i in look)]
    if request is not None:
        starts.append(request.received_at)
    cutoff = min(starts)
    window = dt.timedelta(days=s.same_subject_window_days)

    def in_context(d: DocumentRef) -> bool:
        if d.received_at > cutoff or d.id in look or d.id in conflicted or request is None:
            return True
        if (request.thread_id is not None and d.thread_id == request.thread_id) or d.id == request.document_id:
            return True
        return _norm(d.subject) == _norm(request.subject) != "" and cutoff - d.received_at <= window

    def counts(d: DocumentRef) -> bool:
        return d.channel in COUNTING and (d.channel != "email" or _own(sender_domain(d.sender), domains))

    ctx = frozenset(i for i, d in docs.items() if in_context(d))
    rows = [r for r in kw["sightings"] if r.recorded_at < opened and r.document_id in docs]
    numbers: dict[str, _Number] = {}
    for e164 in sorted({r.e164 for r in rows}):
        mine = [r for r in rows if r.e164 == e164]
        when = {id(r): docs[r.document_id].received_at for r in mine}
        visible = tuple(r for r in mine if r.visibility == "visible")
        vc = tuple(r for r in visible if counts(docs[r.document_id]))
        clean = tuple(r for r in vc if r.document_id not in ctx)
        first_clean = min((when[id(r)] for r in clean), default=None)
        shown = first_clean or min((when[id(r)] for r in visible), default=None) or min(when.values())
        numbers[e164] = _Number(visible, vc, clean, first_clean, shown,
                                frozenset(docs[r.document_id].channel for r in clean))
    return _Frame(docs, cutoff, numbers, ctx)


def _near_flagged(f: _Frame, e164: str, s) -> bool:
    """Would the near-miss rule flag `e164`: an older (or same-time) cleanly seen number within the
    near-miss distance, unless both are past the established line."""
    a = f.numbers[e164]
    mine = a.first_clean or a.shown
    est = dt.timedelta(days=s.established_days)
    for b_e164, b in f.numbers.items():
        if b_e164 == e164 or b.first_clean is None or b.first_clean > mine or not _near(e164, b_e164,
                                                                                         s.near_miss_distance):
            continue
        if a.first_clean is not None and f.cutoff - a.first_clean >= est and f.cutoff - b.first_clean >= est:
            continue
        return True
    return False


def _fax(r: Sighting) -> bool:
    return str(r.role).strip().lower() == "fax"


def _clears_bar(f: _Frame, e164: str, s) -> bool:
    n = f.numbers.get(e164)
    if n is None or not n.visible or not n.clean:
        return False
    if any(r.party != "vendor" or _fax(r) for r in n.visible):
        return False
    if len(n.channels) < s.min_source_types:
        return False
    if f.cutoff - n.first_clean < dt.timedelta(days=s.min_age_days):
        return False
    return not _near_flagged(f, e164, s)


def violations(world: World, out: dict) -> list[str]:
    """The structural invariants (I1-I7, I9) on one select() output."""
    s, found = world.settings, []
    if list(_validator().iter_errors(out)):
        found.append("I6_schema")
    f = _frame(world)
    min_age = dt.timedelta(days=s.min_age_days)
    candidates = out.get("candidates", [])
    for c in candidates:
        if not c.get("eligible"):
            continue
        if c.get("reasons"):
            found.append("I6_pick_has_reasons")
        n = f.numbers.get(c["e164"])
        if n is None:
            found.append("I2_hidden_only_eligible")   # eligible with no sighting the rule could have read
            continue
        if n.visible_counting and all(r.document_id in f.in_context_ids for r in n.visible_counting):
            found.append("I1_incontext_only_eligible")
        if not n.visible:
            found.append("I2_hidden_only_eligible")
        if any(r.party != "vendor" or _fax(r) for r in n.visible):
            found.append("I3_nonvendor_or_fax_eligible")
        if len(n.channels) < s.min_source_types:
            found.append("I4_too_few_channels_eligible")
        if n.first_clean is None or f.cutoff - n.first_clean < min_age:
            found.append("I5_too_young_eligible")
    pick = out.get("e164")
    if pick is not None:
        chosen = next((c for c in candidates if c["e164"] == pick), None)
        if chosen is None or not chosen.get("eligible") or chosen.get("reasons"):
            found.append("I6_pick_has_reasons")
        if pick in world.poison and not _clears_bar(f, pick, s):
            found.append("I7_poison_pick_below_bar")
        if world.kwargs["request"] is None:
            found.append("I9_picked_without_request")
    return sorted(set(found))


def _eligible(out: dict) -> frozenset[str]:
    return frozenset(c["e164"] for c in out.get("candidates", []) if c.get("eligible"))


def _twins(doc: DocumentRef) -> tuple[DocumentRef, DocumentRef]:
    """Two second documents an attacker could file under `doc`'s id. The rule keeps one copy per id,
    the one whose repr sorts first, so one twin sorts after the original (the original stays the copy
    the rule reads) and one sorts before it (the twin becomes that copy)."""
    subject = f"{doc.subject} (shared id)"
    return (dataclasses.replace(doc, sender="zz@other.example", subject=subject),
            dataclasses.replace(doc, sender="", subject=subject))


def _with(world: World, *, documents=None, sightings=None) -> World:
    kw = dict(world.kwargs)
    if documents is not None:
        kw["documents"] = documents
    if sightings is not None:
        kw["sightings"] = sightings
    return dataclasses.replace(world, kwargs=kw)


def metamorphic(world: World, base: dict | None = None) -> list[str]:
    """Re-run the rule on mutated copies of the world. Attacker evidence (an in-context sighting, a
    lookalike email, a second document under an existing id) may block a number but must never make
    one eligible; hidden text and late records must change nothing at all."""
    kw, found = world.kwargs, []
    base = run(world) if base is None else base
    if run(world) != base:
        found.append("M_determinism")
    if run(_with(world, documents=list(reversed(kw["documents"])), sightings=list(reversed(kw["sightings"])))) != base:
        found.append("M_reorder")
    eligible = _eligible(base)
    target = min((c["e164"] for c in base.get("candidates", [])), default=FRESH)
    t_req, domain = kw["requested_cutoff"], kw["vendor_domains"][0]
    thread = kw["request"].thread_id if kw["request"] is not None else "req"

    def plus(doc_id: str, channel: str, days: int, sender: str, thread_id: str, visibility: str) -> World:
        when = t_req - dt.timedelta(days=days)
        doc = DocumentRef(doc_id, channel, when, sender, thread_id, f"Doc {doc_id}", when)
        seen = Sighting(target, doc_id, channel, when, visibility, "ar", "vendor", when)
        return _with(world, documents=[*kw["documents"], doc], sightings=[*kw["sightings"], seen])

    if _eligible(run(plus("m_ctx", "contract", 1, f"ar@{domain}", thread, "visible"))) - eligible:
        found.append("M_incontext_helped")
    hidden = run(plus("m_hidden", "contract", 400, f"ar@{domain}", "t_m_hidden", "white"))
    if _eligible(hidden) != eligible or hidden.get("e164") != base.get("e164"):
        found.append("M_hidden_helped")
    for days in LOOKALIKE_PROBE_DAYS:
        probe = plus(f"m_look{days}", "email", days, _address("lookalike", domain), f"t_m_look{days}", "visible")
        if _eligible(run(probe)) - eligible:
            found.append("M_lookalike_helped")
            break
    opened = kw["case_opened_at"]
    if any(d.recorded_at >= opened for d in kw["documents"]) or any(r.recorded_at >= opened for r in kw["sightings"]):
        trimmed = _with(world, documents=[d for d in kw["documents"] if d.recorded_at < opened],
                        sightings=[r for r in kw["sightings"] if r.recorded_at < opened])
        if run(trimmed) != base:
            found.append("M_late_changed")
    req_id = kw["request"].document_id if kw["request"] is not None else None
    tried: set[str] = set()
    for doc in kw["documents"]:                    # every document, not only the first (I11 is universal)
        if doc.id == req_id or doc.id in tried:
            continue
        tried.add(doc.id)
        if any(_eligible(run(_with(world, documents=[*kw["documents"], twin]))) - eligible for twin in _twins(doc)):
            found.append("M_shared_id_helped")
            break
    return found


def _clean(world: World) -> bool:
    """The L1 clean-world predicate, read from raw inputs (the same knobs clean_worlds draws)."""
    kw, s = world.kwargs, world.settings
    request, opened = kw["request"], kw["case_opened_at"]
    if request is None:
        return False
    domains = tuple(v.strip().lower().rstrip(".") for v in kw["vendor_domains"])
    docs = kw["documents"]
    if len({d.id for d in docs}) < len(docs):
        return False
    cutoff = min(kw["requested_cutoff"], request.received_at)
    for d in docs:
        if d.recorded_at >= opened or d.received_at > cutoff:
            return False
        if d.id == request.document_id:
            continue
        if not _own(sender_domain(d.sender), domains) or d.thread_id == request.thread_id:
            return False
        if _norm(d.subject) == _norm(request.subject):
            return False
    for r in kw["sightings"]:
        if r.recorded_at >= opened or r.visibility != "visible" or r.party != "vendor" or _fax(r):
            return False
    numbers = sorted({r.e164 for r in kw["sightings"]})
    return not any(_near(a, b, s.near_miss_distance) for i, a in enumerate(numbers) for b in numbers[i + 1:])


def l1_premise(world: World) -> bool:
    """A clean world in which some number is seen in enough counting channels, early enough."""
    if not _clean(world):
        return False
    s, f = world.settings, _frame(world)
    return any(len(n.channels) >= s.min_source_types and n.first_clean is not None
               and f.cutoff - n.first_clean >= dt.timedelta(days=s.min_age_days) for n in f.numbers.values())


def liveness(world: World, out: dict) -> list[str]:
    return ["L1_clean_world_no_pick"] if l1_premise(world) and out.get("e164") is None else []


def all_violations(world: World) -> list[str]:
    out = run(world)
    return violations(world, out) + metamorphic(world, out) + liveness(world, out)
