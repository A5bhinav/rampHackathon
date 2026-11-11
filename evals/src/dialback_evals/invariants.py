"""Spec 0083, Task 2: the invariant oracle over select() worlds.

`violations(world, out)` checks the rule's guarantees (I1-I7, I9) on one output. It recomputes
visibility, counting channels, the change context, R2's first-seen order, age and near misses from the
world's raw inputs. Where no document id is shared, that reference follows select()'s frame for the
change context and its cutoff, R2, hidden text, near misses (today's first sighting, labeled or not,
as spec 0091's Q153 keeps it) and spec 0091's rule 3 (`_established_against`), so there those checks
catch regressions (a branch deleted, reasons mis-assembled), not design defects. It does not follow
spec 0091's rule 2: its support for I4 and I5 (channels and age) is today's, every visible sighting in
a counting channel outside the change context, labeled or not, where select() counts only the vendor's
own. That is looser, so I4 and I5 never flag a number select() rightly made eligible, but they can't
see a rule that counts a labeled sighting as support. Where two different documents share an id it is
deliberately conservative, as R7 asks ("both count against"): the id is change context, a lookalike if
any copy is one, and dated by its earliest copy. select() instead keeps the copy whose repr sorts
first, so there the structural checks also catch a current defect (review finding I4). It reuses the
ledger's lookalike-domain classifier and sender parsing (classification, not the decision), so a
classifier bug is invisible to it.
`metamorphic(world)` re-runs the rule on mutated copies of the world (I8, I10, I11, I12, and "attacker
evidence never helps"). `liveness(world, out)` is the L1 over-blocking floor. Every function returns
violation labels; an empty list means the rule held.

Spec 0046: `metamorphic` and `all_violations` take two optional keywords. `rule` is a rule module (an
object with `select` and `change_context_documents`, such as a pinned or broken copy of select.py) to
check instead of the working tree's; without it they call `run`, which tests replace. `probes` is a
`Probes` recorder that metamorphic() fills with the base output and the output of every must-not-help
probe it runs, for the guided search's score. Neither changes which runs are made or any label.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.resources
import json
import re
from functools import lru_cache
from pathlib import Path

from dialback_ledger.lookalike import is_lookalike, sender_domain
from dialback_ledger.select import DocumentRef, Sighting, change_context_documents
from jsonschema import Draft202012Validator

from .worldgen import World, run, sender_address

# The must-not-help moves (spec 0046): the probes below that add attacker evidence, by the label each
# one reports when the evidence makes a number eligible.
MUST_NOT_HELP = {"incontext": "M_incontext_helped", "lookalike": "M_lookalike_helped",
                 "shared_id": "M_shared_id_helped"}

COUNTING = frozenset({"email", "contract", "vendor_portal", "listing", "paper_mail"})  # not vendor_master
FRESH = "+10895550199"   # fictional, at least 3 digits from every generated number; a probe target
LOOKALIKE_PROBE_DAYS = (30, 100, 400)   # a lookalike email this many days before the request
SCHEMA = "packages/contracts/schemas/selection.schema.json"
_PREFIX = re.compile(r"^((re|fwd?|aw|sv)\s*:|\[[^\]]*\])\s*")


def schema_path(start: Path | None = None) -> Path:
    """The selection schema, found by walking up from the package (or `start`) to the checkout that holds
    the contracts, so it no longer depends on how deep the package sits (FINDINGS B7, M13)."""
    here = Path(start if start is not None else str(importlib.resources.files("dialback_evals"))).resolve()
    for folder in (here, *here.parents):
        if (folder / SCHEMA).is_file():
            return folder / SCHEMA
    raise FileNotFoundError(f"no {SCHEMA} in {here} or above it: run the lab from a checkout of the repository")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_path().read_text(encoding="utf-8")))


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


def _counts(d: DocumentRef, domains: tuple[str, ...]) -> bool:
    """A channel that counts: a known one, and for email only the vendor's own domains."""
    return d.channel in COUNTING and (d.channel != "email" or _own(sender_domain(d.sender), domains))


@dataclasses.dataclass
class Probes:
    """What metamorphic() saw on a world (spec 0046): the base output, and each must-not-help probe it
    ran, in order, as (move, probe, output). Filled only when passed in; no label reads it."""
    base: dict | None = None
    runs: list[tuple[str, str, dict]] = dataclasses.field(default_factory=list)


def _keep(probes: Probes | None, move: str, probe: str, out: dict) -> dict:
    if probes is not None:
        probes.runs.append((move, probe, out))
    return out


def _runner(rule):
    """The select() a check calls: `run` (looked up when called, so tests can replace it), or the given
    rule module's own select()."""
    if rule is None:
        return run
    return lambda world: rule.select(**world.kwargs)


@dataclasses.dataclass(frozen=True)
class _Number:
    visible: tuple[Sighting, ...]
    first_seen_in_context: bool          # R2: the earliest visible sighting is in context (ties count)
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
    copies: dict[str, list[DocumentRef]] = {}
    for d in kw["documents"]:
        if d.recorded_at < opened:
            copies.setdefault(d.id, []).append(d)
    conflicted = {i for i, cs in copies.items() if len(set(cs)) > 1}          # two different documents, one id
    docs = {i: min(cs, key=lambda d: (d.received_at, repr(d))) for i, cs in copies.items()}   # earliest copy
    look = {i for i, cs in copies.items()
            if any((sd := sender_domain(d.sender)) and is_lookalike(sd, domains) for d in cs)}  # any copy
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

    ctx = frozenset(i for i, d in docs.items() if in_context(d))
    rows = [r for r in kw["sightings"] if r.recorded_at < opened and r.document_id in docs]
    numbers: dict[str, _Number] = {}
    for e164 in sorted({r.e164 for r in rows}):
        mine = [r for r in rows if r.e164 == e164]
        when = {id(r): docs[r.document_id].received_at for r in mine}
        visible = tuple(r for r in mine if r.visibility == "visible")
        ctx_times = [when[id(r)] for r in visible if r.document_id in ctx]
        out_times = [when[id(r)] for r in visible if r.document_id not in ctx]
        first_seen_ctx = bool(ctx_times) and (not out_times or min(ctx_times) <= min(out_times))
        clean = tuple(r for r in visible if _counts(docs[r.document_id], domains) and r.document_id not in ctx)
        first_clean = min((when[id(r)] for r in clean), default=None)
        shown = first_clean or min((when[id(r)] for r in visible), default=None) or min(when.values())
        numbers[e164] = _Number(visible, first_seen_ctx, clean, first_clean, shown,
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


def _established_against(f: _Frame, n: _Number, world: World) -> bool:
    """Spec 0091, rule 3 (its fourth revision, Q162), re-derived from the lab's own frame so the oracle
    stays independent of the ledger's: are the labels against this number established? A label against
    is a visible sighting that isn't the vendor's, or is a fax. Its document counts only by its own
    facts: a counting channel, received at least `min_age_days` before the request (the earlier of the
    requested cutoff and its receipt), and not a lookalike's. So the cutoff a lookalike pulls back, the
    request's thread and subject, and an id shared with another document play no part, and the request
    itself, never that old, never counts. Established means the documents that count span
    `min_source_types` channels. With no request there is nothing to measure from, so no label is
    established."""
    kw, s = world.kwargs, world.settings
    request = kw["request"]
    if request is None:
        return False
    domains = tuple(v.strip().lower().rstrip(".") for v in kw["vendor_domains"])
    asked = min(kw["requested_cutoff"], request.received_at)
    old_enough = dt.timedelta(days=s.min_age_days)

    def stands(d: DocumentRef) -> bool:
        if not _counts(d, domains) or asked - d.received_at < old_enough:
            return False
        return not ((sd := sender_domain(d.sender)) and is_lookalike(sd, domains))

    labeling = [f.docs[r.document_id] for r in n.visible if r.party != "vendor" or _fax(r)]
    channels = {d.channel for d in labeling if stands(d)}
    return bool(channels) and len(channels) >= s.min_source_types


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
        if n.first_seen_in_context:
            found.append("I1_first_seen_in_context_eligible")
        if not n.visible:
            found.append("I2_hidden_only_eligible")
        # Only an established label refuses a number (spec 0091, rule 3). A label younger than
        # `min_age_days`, such as the request's own or anything sent with it, can't establish anything,
        # so I12 judges what those may do (I1 still refuses a number first seen in the context).
        if _established_against(f, n, world):
            found.append("I3_nonvendor_or_fax_eligible")
        if len(n.channels) < s.min_source_types:
            found.append("I4_too_few_channels_eligible")
        if n.first_clean is None or f.cutoff - n.first_clean < min_age:
            found.append("I5_too_young_eligible")
        if _near_flagged(f, c["e164"], s):              # every eligible number, poison or not (review I4)
            found.append("I7_near_miss_eligible")
    pick = out.get("e164")
    if pick is not None:
        chosen = next((c for c in candidates if c["e164"] == pick), None)
        if chosen is None or not chosen.get("eligible") or chosen.get("reasons"):
            found.append("I6_pick_has_reasons")
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


def metamorphic(world: World, base: dict | None = None, *, rule=None, probes: Probes | None = None) -> list[str]:
    """Re-run the rule on mutated copies of the world. Attacker evidence (an in-context sighting, a
    lookalike email, a second document under an existing id) must never make a number eligible; hidden
    text and late records must change nothing at all; and a label in the change context must never
    move the call to a different number (I12, gated Nov 8)."""
    kw, found, call = world.kwargs, [], _runner(rule)
    base = call(world) if base is None else base
    if probes is not None:
        probes.base = base
    if call(world) != base:
        found.append("M_determinism")
    if call(_with(world, documents=list(reversed(kw["documents"])), sightings=list(reversed(kw["sightings"])))) != base:
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

    # These two probes put their document in the request's thread. A request with no thread opens no
    # thread path (spec 0048, C2), so there they do not apply: their document would sit outside the
    # rule's change context, where a clean contract sighting may rightly help.
    threaded = thread is not None
    if threaded and _eligible(_keep(probes, "incontext", "m_ctx", call(plus("m_ctx", "contract", 1, f"ar@{domain}",
                                                                              thread, "visible")))) - eligible:
        found.append("M_incontext_helped")
    rows = [r for r in kw["sightings"] if r.e164 == target]
    if threaded and rows and kw["request"] is not None:   # R2: a sighting in context older than all others blocks it
        days = (t_req - min(r.received_at for r in rows)).days + 1
        if target in _eligible(call(plus("m_first", "contract", days, f"ar@{domain}", thread, "visible"))):
            found.append("M_first_seen_ignored")
    hidden = call(plus("m_hidden", "contract", 400, f"ar@{domain}", "t_m_hidden", "white"))
    if _eligible(hidden) != eligible or hidden.get("e164") != base.get("e164"):
        found.append("M_hidden_helped")
    for days in LOOKALIKE_PROBE_DAYS:
        probe = plus(f"m_look{days}", "email", days, sender_address("lookalike", domain), f"t_m_look{days}", "visible")
        if _eligible(_keep(probes, "lookalike", f"m_look{days}", call(probe))) - eligible:
            found.append("M_lookalike_helped")
            break
    opened = kw["case_opened_at"]
    if any(d.recorded_at >= opened for d in kw["documents"]) or any(r.recorded_at >= opened for r in kw["sightings"]):
        trimmed = _with(world, documents=[d for d in kw["documents"] if d.recorded_at < opened],
                        sightings=[r for r in kw["sightings"] if r.recorded_at < opened])
        if call(trimmed) != base:
            found.append("M_late_changed")
    req_id = kw["request"].document_id if kw["request"] is not None else None
    tried: set[str] = set()
    for doc in kw["documents"]:                    # every document, not only the first (I11 is universal)
        if doc.id == req_id or doc.id in tried:
            continue
        tried.add(doc.id)
        helped = False
        for k, twin in enumerate(_twins(doc)):    # stops at the first twin that helps, as any() did
            out = _keep(probes, "shared_id", f"twin{k}:{doc.id}", call(_with(world, documents=[*kw["documents"], twin])))
            if _eligible(out) - eligible:
                helped = True
                break
        if helped:
            found.append("M_shared_id_helped")
            break
    if moved := _label_probe(world, base, rule):
        found.append(moved)
    return found


# I12's attacker text, in the shapes ingestion emits (spec 0022's stub reader): a bank's line, both
# without a role ("Bank verification line: ...") and with one ("Remit to our bank: ..." gives role ar),
# a fax line, a bare mention with no label the reader can place (role and party "unknown"), which the
# rule also refuses, and a customer's line, which a model reader may emit.
LABEL_KINDS = (("unknown", "bank"), ("ar", "bank"), ("fax", "vendor"), ("unknown", "unknown"), ("unknown", "customer"))


def _context_labels_removed(world: World, rule=None) -> World:
    """The world with every sighting in the rule's own change context relabeled the vendor's and not a
    fax: the same documents, times and visibility, without what the attacker's text says about them.
    It uses select()'s change context, not this oracle's conservative one, so a shared id the rule
    reads as clean keeps its labels and a fixed rule is never flagged for the frame's own defect."""
    kw = world.kwargs
    context = change_context_documents if rule is None else rule.change_context_documents
    ctx = {d.id for d in context(**kw)}
    return _with(world, sightings=[dataclasses.replace(s, party="vendor", role="main") if s.document_id in ctx else s
                                   for s in kw["sightings"]])


def _calls(out: dict) -> list[str]:
    """The numbers an output would call, in order: the pick, then its alternates."""
    return ([out["e164"]] if out.get("e164") else []) + [a["e164"] for a in out.get("alternates", [])]


def _label_probe(world: World, base: dict, rule=None) -> str | None:
    """I12: a label in the change context never moves a call to a different number; refusing the case,
    or calling fewer backups, is allowed. Two halves. Removal: with the labels already in the context
    made neutral, what the rule calls (the pick, then its alternates) must be the start of what it
    would call then, so a label may trim the list but never change it (re-review K2). Addition: the attacker's
    own text names the picked number, one label kind on one context path at a time, so no probe can
    hide another: the request email itself; a reply in its thread from another domain and from the
    vendor's own; an email under its subject, and under a forwarded copy of it, in another thread; a
    homoglyph and a hyphenated lookalike sender; and a follow-up after the request, in a thread of its
    own, from the vendor's own domain and from another (in the context only because it came after the
    cutoff). The call must stay on that number or go nowhere. The addition half runs only when the
    pick was seen outside the context before the request, so R2's first-seen rule never comes into play."""
    kw, request, pick = world.kwargs, world.kwargs["request"], base.get("e164")
    if pick is None:
        return None
    call = _runner(rule)
    calls, without = _calls(base), _calls(call(_context_labels_removed(world, rule)))
    if calls != without[:len(calls)]:
        return "M_incontext_label_moved_pick"
    if request is None or request.received_at >= kw["case_opened_at"]:
        return None
    when, f = request.received_at, _frame(world)
    number = f.numbers.get(pick)
    if number is None or not any(r.document_id not in f.in_context_ids and f.docs[r.document_id].received_at < when
                                 for r in number.visible):
        return None
    domain = kw["vendor_domains"][0]
    other, own = "ar@other.example", f"ar@{domain}"
    after = when + (kw["case_opened_at"] - when) / 2      # a follow-up between the request and the case opening
    # The request email itself first: real ingestion puts the attacker's words in its body (review 2, B1).
    paths: list[tuple[str, list[DocumentRef], dt.datetime]] = []
    if request.document_id in f.docs:
        paths.append((request.document_id, kw["documents"], when))
    # A reply in the request's thread exists only when the request has a thread (spec 0048, C2); without
    # one these two documents would sit outside the rule's change context, so they are not built.
    probes = [] if request.thread_id is None else [
        DocumentRef("m_lbl_thread", "email", when, other, request.thread_id, "Doc m_lbl_thread", when),
        DocumentRef("m_lbl_thread_own", "email", when, own, request.thread_id, "Doc m_lbl_thread_own", when)]
    if request.subject:                                 # likewise an email under its subject (C2's bare request)
        probes.append(DocumentRef("m_lbl_subject", "email", when, other, "t_m_lbl_subject", request.subject, when))
    probes += [DocumentRef("m_lbl_look", "email", when, sender_address("lookalike", domain), "t_m_lbl_look",
                          "Doc m_lbl_look", when),
              DocumentRef("m_lbl_combo", "email", when, sender_address("combosquat", domain), "t_m_lbl_combo",
                          "Doc m_lbl_combo", when),
              DocumentRef("m_lbl_after_own", "email", after, own, "t_m_lbl_after_own", "Doc m_lbl_after_own", after),
              DocumentRef("m_lbl_after_other", "email", after, other, "t_m_lbl_after_other", "Doc m_lbl_after_other",
                          after)]
    if request.subject:
        probes.append(DocumentRef("m_lbl_fwd", "email", when, other, "t_m_lbl_fwd", f"Fwd: {request.subject}", when))
    paths += [(doc.id, [*kw["documents"], doc], doc.received_at) for doc in probes]
    for doc_id, documents, at in paths:
        if doc_id not in _frame(_with(world, documents=documents)).in_context_ids:
            continue                                   # a path this world's request does not open
        for role, party in LABEL_KINDS:
            row = Sighting(pick, doc_id, "email", at, "visible", role, party, at)
            now = call(_with(world, documents=documents, sightings=[*kw["sightings"], row])).get("e164")
            if now is not None and now != pick:
                return "M_incontext_label_moved_pick"
    return None


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


def all_violations(world: World, *, rule=None, probes: Probes | None = None) -> list[str]:
    out = _runner(rule)(world)
    return violations(world, out) + metamorphic(world, out, rule=rule, probes=probes) + liveness(world, out)
