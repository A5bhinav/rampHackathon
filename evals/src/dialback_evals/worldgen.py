"""Spec 0033, Tasks 1 and 1b: generate select() input worlds, parametric over what an attacker controls.

A `World` holds the full `select()` keyword inputs plus the set of e164s the generator seeded as
attacker-controlled (`poison`). `make_world` is the deterministic builder (tests and the sweeps use
it); `worlds()` draws an adversarial world and `clean_worlds()` a world with no attacker knob set.
Every knob is per sighting (one document per sighting, except a sighting in the request email
itself), so one number can be visible in one document and hidden in another. The lab imports only the pure `dialback_ledger` selection rule and
its helpers; it never touches the rule's behavior.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field

from dialback_ledger.lookalike import is_lookalike, sender_domain
from dialback_ledger.select import ChangeRequest, DocumentRef, Settings, Sighting, near_miss, select
from hypothesis import assume
from hypothesis import strategies as st

DEFAULT = Settings()
T0 = dt.datetime(2026, 10, 1, 16, 0, tzinfo=dt.UTC)
CASE, DATASET, VENDOR = (uuid.UUID(int=n) for n in (1, 2, 3))
VENDOR_DOMAIN = "acme.example"
REQUEST_SUBJECT = "Updated banking details"
REPLY_PREFIXES = ("Re: ", "Fwd: ", "[EXTERNAL] ")
SUBJECT_PREFIX = {"re": "Re: ", "fwd": "Fwd: ", "external": "[EXTERNAL] "}
# Every number the lab writes is in the NANP block reserved for fiction (NPA-555-0100 to 0199).
FICTIONAL = re.compile(r"^\+1\d{3}55501\d{2}$")
# Area codes whose digits differ pairwise in all three places: two numbers in different ones differ in
# at least three digits, so unrelated numbers are never near misses (at most 2 digits, or a swap).
FAR_NPAS = ("201", "312", "423", "534", "645", "756", "867", "978")

COUNTING = ["email", "contract", "vendor_portal", "listing", "paper_mail"]  # vendor_master never counts
CHANNELS = [*COUNTING, "vendor_master"]
VISIBILITIES = ["visible", "white", "tiny", "off_page"]
ROLES = ["main", "ar", "billing", "fax", "unknown"]
PARTIES = ["vendor", "bank", "customer", "unknown"]
SENDERS = ["own", "subdomain", "lookalike", "combosquat", "other"]
SUBJECTS = ["other", "same", "re", "fwd", "external"]


@dataclass(frozen=True)
class SightingSpec:
    """One sighting of a number, in its own document, with its own attacker knobs."""
    channel: str
    lead_days: int                   # days before the change request this document was received
    visibility: str = "visible"      # visible | white | tiny | off_page
    role: str = "ar"                 # main | ar | billing | fax | unknown
    party: str = "vendor"            # vendor | bank | customer | unknown
    sender: str = "own"              # own | subdomain | lookalike | combosquat | other
    in_thread: bool = False          # the document sits in the change request's thread
    subject: str = "other"           # other | same | re | fwd | external (the last three reply to it)
    recorded_late: bool = False      # recorded into the ledger after the case opened
    shared_id: bool = False          # a second, different document is filed under the same id
    twin_first: bool = False         # that second document sorts first, so select() reads it instead
    on_request: bool = False         # in the change request email's own body, not a document of its own
                                     # (it then takes the request's time, sender and thread; the other
                                     # document knobs do not apply; with no request it is a normal sighting)
    after_request: bool = False      # a follow-up received 30 minutes after the request, before the case opens


@dataclass(frozen=True)
class NumberSpec:
    """One candidate number. Either a per-number profile expanded over `channels`, or explicit
    per-sighting `sightings` (which, when set, replace the profile fields)."""
    e164: str
    channels: list[str]
    lead_days: int                    # days before the change request it was first seen
    visibility: str = "visible"
    role: str = "ar"
    party: str = "vendor"
    sender: str = "own"               # any SENDERS value
    in_thread: bool = False
    subject_in_context: bool = False  # the documents share the change request's subject
    near_miss_of: str | None = None   # informational: the e164 this one is a near-miss of
    is_poison: bool = False           # the generator's intent: an attacker-controlled number
    sightings: tuple[SightingSpec, ...] = ()

    def expand(self) -> tuple[SightingSpec, ...]:
        if self.sightings:
            return self.sightings
        subject = "same" if self.subject_in_context else "other"
        return tuple(SightingSpec(ch, self.lead_days, self.visibility, self.role, self.party, self.sender,
                                  self.in_thread, subject) for ch in self.channels)


@dataclass(frozen=True)
class World:
    kwargs: dict = field(default_factory=dict)   # the full select() keyword inputs
    poison: frozenset[str] = frozenset()         # e164s seeded as attacker-controlled
    settings: Settings = DEFAULT


def _address(sender: str, domain: str) -> str:
    host, _, suffix = domain.partition(".")
    tail = f".{suffix}" if suffix else ""
    if sender == "subdomain":
        return f"ar@billing.{domain}"
    if sender == "lookalike":
        twin = host.replace("m", "rn", 1) if "m" in host else host + "x"
        return f"ar@{twin}{tail}"
    if sender == "combosquat":
        return f"ar@{host}-billing{tail}"
    if sender == "other":
        return "ar@other.example"
    return f"ar@{domain}"


def _subject_line(kind: str, label: str) -> str:
    if kind == "same":
        return REQUEST_SUBJECT
    if kind in SUBJECT_PREFIX:
        return SUBJECT_PREFIX[kind] + REQUEST_SUBJECT
    return f"Doc {label}"


def make_world(*, numbers: list[NumberSpec], vendor_domains: tuple[str, ...] = (VENDOR_DOMAIN,),
               request_at: dt.datetime = T0, settings: Settings = DEFAULT, request_present: bool = True,
               cutoff_lead_days: int = 0) -> World:
    """Build the select() inputs: one document (and one sighting) per sighting spec, except a sighting
    in the request email itself. `cutoff_lead_days` puts the requested cutoff (the change in Ramp)
    that many days before the request email, as when Ramp's change came first."""
    domain = vendor_domains[0]
    opened = request_at + dt.timedelta(hours=1)
    late_at = opened + dt.timedelta(days=1)
    documents: list[DocumentRef] = []
    sightings: list[Sighting] = []
    request = None
    if request_present:
        documents.append(DocumentRef("req", "email", request_at, f"ar@{domain}", "req", REQUEST_SUBJECT, request_at))
        request = ChangeRequest("req", request_at, "req", REQUEST_SUBJECT, f"ar@{domain}")
    for i, spec in enumerate(numbers):
        for j, sp in enumerate(spec.expand()):
            if sp.on_request and request is not None:   # the attacker's words in the request email itself
                sightings.append(Sighting(spec.e164, request.document_id, "email", request_at, sp.visibility,
                                          sp.role, sp.party, request_at))
                continue
            label = f"{i}-{j}"
            doc_id = f"d{label}"
            received = (request_at + dt.timedelta(minutes=30) if sp.after_request
                        else request_at - dt.timedelta(days=sp.lead_days))
            recorded = late_at if sp.recorded_late else received
            thread = "req" if sp.in_thread else f"t{label}"
            subject = _subject_line(sp.subject, label)
            documents.append(DocumentRef(doc_id, sp.channel, received, _address(sp.sender, domain), thread,
                                         subject, recorded))
            if sp.shared_id:
                twin_sender = "" if sp.twin_first else "ar@other.example"   # "" sorts before every "ar@" sender
                documents.append(DocumentRef(doc_id, sp.channel, received, twin_sender, thread,
                                             f"{subject} (second copy)", recorded))
            sightings.append(Sighting(spec.e164, doc_id, sp.channel, received, sp.visibility, sp.role,
                                      sp.party, recorded))
    kwargs = {"case_id": CASE, "dataset_id": DATASET, "vendor_id": VENDOR, "vendor_domains": tuple(vendor_domains),
              "request": request, "requested_cutoff": request_at - dt.timedelta(days=cutoff_lead_days),
              "case_opened_at": opened,
              "documents": documents, "sightings": sightings, "settings": settings}
    poison = frozenset(s.e164 for s in numbers if s.is_poison)
    return World(kwargs=kwargs, poison=poison, settings=settings)


def run(world: World) -> dict:
    """Run the real selection rule on a world."""
    return select(**world.kwargs)


def profile(world: World) -> Counter[str]:
    """The knob paths present in a world, read from its raw select() inputs (1 present, 0 absent)."""
    kw = world.kwargs
    docs, rows, opened = kw["documents"], kw["sightings"], kw["case_opened_at"]
    domains = tuple(kw["vendor_domains"])
    sender_domains = [sd for d in docs if (sd := sender_domain(d.sender))]
    by_number: dict[str, list[Sighting]] = {}
    for s in rows:
        by_number.setdefault(s.e164, []).append(s)
    numbers = sorted(by_number)
    p: Counter[str] = Counter()
    p["no_request"] = int(kw["request"] is None)
    p["late"] = int(any(d.recorded_at >= opened for d in docs) or any(s.recorded_at >= opened for s in rows))
    p["shared_id"] = int(len({d.id for d in docs}) < len(docs))
    p["reply_subject"] = int(any((d.subject or "").startswith(REPLY_PREFIXES) for d in docs))
    p["subdomain"] = int(any(sd != v and sd.endswith("." + v) for sd in sender_domains for v in domains))
    p["lookalike"] = int(any(is_lookalike(sd, domains) for sd in sender_domains))
    p["near_miss_pair"] = int(any(near_miss(a, b, 2) for i, a in enumerate(numbers) for b in numbers[i + 1:]))
    p["mixed_visibility"] = int(any(len({s.visibility == "visible" for s in v}) > 1 for v in by_number.values()))
    p["mixed_party"] = int(any(len({s.party for s in v}) > 1 for v in by_number.values()))
    p["mixed_timing"] = int(any(len({s.received_at for s in v}) > 1 for v in by_number.values()))
    p["non_default_settings"] = int(world.settings != DEFAULT)
    p["twin_first"] = int(any(d.sender == "" for d in docs))
    request = kw["request"]
    p["follow_up"] = int(request is not None and any(      # a document after the request, before the case opened
        d.received_at > request.received_at and d.recorded_at < opened for d in docs))
    p["early_cutoff"] = int(request is not None and kw["requested_cutoff"] < request.received_at)
    p["request_label"] = int(request is not None and any(   # the attacker's words in the request email itself
        s.document_id == request.document_id and s.visibility == "visible"
        and (s.party != "vendor" or str(s.role).strip().lower() == "fax") for s in rows))
    return p


def fictional(e164: str) -> bool:
    return bool(FICTIONAL.match(e164))


def _fictional_e164(npa: str, line: int) -> str:
    """A number in the block reserved for fiction: NPA-555-0100 to NPA-555-0199."""
    return f"+1{npa}55501{line:02d}"


def _sibling(e164: str, mode: str, which: int, bump: int) -> str:
    """A near miss of `e164` that stays in the fictional block: only its last two digits move. One of
    them changes (`which` picks which one), the two swap, or both change."""
    head, a, b = e164[:-2], int(e164[-2]), int(e164[-1])
    step = 1 + bump % 9
    if mode == "swap" and a != b:
        a, b = b, a
    elif mode == "two":
        a, b = (a + step) % 10, (b + step) % 10
    elif which % 2:
        b = (b + step) % 10
    else:
        a = (a + step) % 10
    return f"{head}{a}{b}"


SETTINGS = st.one_of(st.just(DEFAULT), st.builds(
    Settings, min_source_types=st.integers(1, 3), min_age_days=st.sampled_from([0, 30, 90, 180]),
    near_miss_distance=st.integers(0, 2), same_subject_window_days=st.sampled_from([0, 7, 30, 90])))

_SIGHTING = st.builds(
    SightingSpec,
    channel=st.sampled_from(CHANNELS),
    lead_days=st.integers(0, 800),
    visibility=st.sampled_from(["visible", "visible", "visible", "white", "tiny", "off_page"]),
    role=st.sampled_from(ROLES),
    party=st.sampled_from(["vendor", "vendor", "vendor", "bank", "customer", "unknown"]),
    # Lookalike and combosquat senders are 1 in 20 sightings each: one old lookalike email pulls the
    # cutoff back for the whole world, so a higher rate leaves most worlds with nothing eligible and
    # the safety invariants trivially true (ledger, Task 1b ruling).
    sender=st.sampled_from(["own"] * 12 + ["subdomain"] * 3 + ["lookalike", "combosquat"] + ["other"] * 3),
    in_thread=st.sampled_from([False, False, False, True]),
    subject=st.sampled_from(["other", "other", "other", "same", "re", "fwd", "external"]),
    recorded_late=st.sampled_from([False] * 9 + [True]),
    shared_id=st.sampled_from([False] * 9 + [True]),
    twin_first=st.booleans(),
)


@st.composite
def _number(draw, e164: str, near_miss_of: str | None = None) -> NumberSpec:
    sightings = tuple(draw(st.lists(_SIGHTING, min_size=1, max_size=3)))
    return NumberSpec(e164=e164, channels=[], lead_days=0, near_miss_of=near_miss_of,
                      is_poison=draw(st.booleans()), sightings=sightings)


def _real(e164: str) -> NumberSpec:
    """The plainly real, established vendor line every adversarial world ranks against."""
    return NumberSpec(e164=e164, channels=["contract", "listing"], lead_days=500)


@st.composite
def worlds(draw, settings: Settings | None = None) -> World:
    """Draw an adversarial world: settings (drawn when `settings` is None), 1 to 5 attacker-shaped
    numbers with independent per-sighting knobs, 0 to 2 deliberate near-miss siblings, one real,
    established vendor line, so the rule always has a legitimate pick to rank against, and sometimes
    a planted provable number, an attacker's label naming one of the two (in the request email, a
    thread reply, or a follow-up after the request), and a cutoff a day before the request."""
    chosen = settings if settings is not None else draw(SETTINGS)
    count = draw(st.integers(1, 5))
    npas = draw(st.lists(st.sampled_from(FAR_NPAS[:-1]), min_size=count, max_size=count, unique=True))
    specs = [draw(_number(_fictional_e164(npa, draw(st.integers(0, 99))))) for npa in npas]
    real = _fictional_e164(FAR_NPAS[-1], 0)
    specs.append(_real(real))
    others = [s.e164 for s in specs[:-1]]
    taken = {s.e164 for s in specs}
    for _ in range(draw(st.integers(0, 2))):
        base = draw(st.sampled_from([real] * len(others) + others))   # half the siblings shadow the real line
        sib = _sibling(base, draw(st.sampled_from(["one", "swap", "two"])), draw(st.integers(0, 1)),
                       draw(st.integers(0, 8)))
        if sib not in taken:
            taken.add(sib)
            specs.append(draw(_number(sib, near_miss_of=base)))
    # A planted provable number (K01's shape: contract + listing, vendor-labeled, visible) in two thirds
    # of the worlds, at a drawn age, so the rule often has a second number the call could move to (I12).
    # At one half the two-provable rate sat at its 20% floor (Nov 8 attempts, spec 0033). Its area
    # code is one no other number uses, so it is never a near miss by accident.
    targets = [real]
    if draw(st.sampled_from([False, True, True])):
        free = [npa for npa in FAR_NPAS[:-1] if npa not in npas]
        planted = _fictional_e164(draw(st.sampled_from(free)), draw(st.integers(0, 99)))
        specs.append(NumberSpec(planted, ["contract", "listing"], draw(st.integers(0, 700)), is_poison=True))
        targets.append(planted)
    # In half the worlds with a planted number, the attacker's own words name one of the two, in the
    # shapes ingestion emits: a bank's line, a fax line, a bare mention the reader can't place (party
    # "unknown") or a customer's line. They sit in the request email itself (where real ingestion puts
    # them), in a reply in its thread from another domain, or in one from the vendor's own domain (a
    # hijacked mailbox). Only with a planted number does a label have a second number to hand the call to.
    if len(targets) > 1 and draw(st.booleans()):
        role, party = draw(st.sampled_from([("unknown", "bank"), ("fax", "vendor"), ("unknown", "unknown"),
                                            ("unknown", "customer")]))
        # The request email counts twice: it is where real ingestion puts the attacker's words.
        where = draw(st.sampled_from(["request", "request", "thread", "thread_own", "after_own", "after_other"]))
        label = SightingSpec("email", 0 if where in ("request", "after_own", "after_other") else draw(st.integers(0, 30)),
                             role=role, party=party, sender="own" if where.endswith("own") else "other",
                             in_thread=where in ("thread", "thread_own"), on_request=where == "request",
                             after_request=where.startswith("after"))
        specs.append(NumberSpec(draw(st.sampled_from(targets)), [], 0, sightings=(label,)))
    request_present = draw(st.sampled_from([True] * 9 + [False]))
    # In one world in eight, Ramp's change came before the request email, so the cutoff is earlier and
    # anything received between the two is in the change context (re-review K1).
    cutoff_lead = draw(st.sampled_from([0] * 7 + [1]))
    return make_world(numbers=specs, settings=chosen, request_present=request_present, cutoff_lead_days=cutoff_lead)


_CLEAN_SIGHTING = st.builds(SightingSpec, channel=st.sampled_from(CHANNELS), lead_days=st.integers(0, 800),
                            role=st.sampled_from(["main", "ar", "billing", "unknown"]),
                            sender=st.sampled_from(["own", "subdomain"]))


@st.composite
def clean_worlds(draw, settings: Settings | None = None) -> World:
    """Draw a world with no attacker knob set (the L1 liveness floor), plus one number built to meet
    the L1 premise: the first `min_source_types` counting channels, at least `min_age_days + 30` old."""
    chosen = settings if settings is not None else draw(SETTINGS)
    count = draw(st.integers(0, 3))
    npas = draw(st.lists(st.sampled_from(FAR_NPAS), min_size=count + 1, max_size=count + 1, unique=True))
    specs = [NumberSpec(e164=_fictional_e164(npa, draw(st.integers(0, 99))), channels=[], lead_days=0,
                        sightings=tuple(draw(st.lists(_CLEAN_SIGHTING, min_size=1, max_size=3)))) for npa in npas[:-1]]
    lead = chosen.min_age_days + 30 + draw(st.integers(0, 400))
    specs.append(NumberSpec(e164=_fictional_e164(npas[-1], 23), channels=COUNTING[:chosen.min_source_types],
                            lead_days=lead))
    numbers = [s.e164 for s in specs]
    assume(not any(near_miss(a, b, 2) for i, a in enumerate(numbers) for b in numbers[i + 1:]))   # true by construction
    return make_world(numbers=specs, settings=chosen)
