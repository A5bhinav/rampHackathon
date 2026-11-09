"""Spec 0030, Task 1: generate select() input worlds, parametric over what an attacker controls.

A `World` holds the full `select()` keyword inputs plus the set of e164s the generator seeded as
attacker-controlled (`poison`). `make_world` is the deterministic builder (tests and the frontier
use it); `worlds()` is the Hypothesis strategy that draws one. The lab imports only the pure
`dialback_ledger.select` function and its dataclasses; it never touches the rule's behavior.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from dialback_ledger.select import ChangeRequest, DocumentRef, Settings, Sighting, select
from hypothesis import strategies as st

DEFAULT = Settings()
T0 = dt.datetime(2026, 10, 1, 16, 0, tzinfo=dt.UTC)
CASE, DATASET, VENDOR = (uuid.UUID(int=n) for n in (1, 2, 3))
VENDOR_DOMAIN = "acme.example"
REQUEST_SUBJECT = "Updated banking details"

COUNTING = ["email", "contract", "vendor_portal", "listing", "paper_mail"]  # vendor_master never counts
CHANNELS = [*COUNTING, "vendor_master"]
VISIBILITIES = ["visible", "white", "tiny", "off_page"]
ROLES = ["main", "ar", "billing", "fax", "unknown"]
PARTIES = ["vendor", "bank", "customer", "unknown"]
SENDERS = ["own", "lookalike", "other"]


@dataclass(frozen=True)
class NumberSpec:
    """One candidate number and the attacker knobs for how it was seen."""
    e164: str
    channels: list[str]
    lead_days: int                    # days before the change request it was first seen
    visibility: str = "visible"       # visible | white | tiny | off_page
    role: str = "ar"                  # main | ar | billing | fax | unknown
    party: str = "vendor"             # vendor | bank | customer | unknown
    sender: str = "own"               # own | lookalike | other (the document's sender domain)
    in_thread: bool = False           # the document shares the change request's thread_id
    subject_in_context: bool = False  # the document shares the change request's subject
    near_miss_of: str | None = None   # informational: the e164 this one is a near-miss of
    is_poison: bool = False           # the generator's intent: an attacker-controlled number


@dataclass(frozen=True)
class World:
    kwargs: dict = field(default_factory=dict)   # the full select() keyword inputs
    poison: frozenset[str] = frozenset()         # e164s seeded as attacker-controlled
    settings: Settings = DEFAULT


def _lookalike(domain: str) -> str:
    host, _, rest = domain.partition(".")
    twin = host.replace("m", "rn", 1) if "m" in host else host + "x"
    return f"{twin}.{rest}" if rest else twin


def _sender(spec: NumberSpec, domain: str) -> str:
    if spec.sender == "lookalike":
        return f"ar@{_lookalike(domain)}"
    if spec.sender == "other":
        return "ar@other.example"
    return f"ar@{domain}"


def make_world(*, numbers: list[NumberSpec], vendor_domains: tuple[str, ...] = (VENDOR_DOMAIN,),
               request_at: dt.datetime = T0, settings: Settings = DEFAULT) -> World:
    """Build the select() inputs for a set of numbers. One document (and sighting) per number per
    channel; all of a number's sightings share its visibility, role, party, sender and context."""
    domain = vendor_domains[0]
    opened = request_at + dt.timedelta(hours=1)
    req_doc = DocumentRef("req", "email", request_at, f"ar@{domain}", "req", REQUEST_SUBJECT, request_at)
    request = ChangeRequest("req", request_at, "req", REQUEST_SUBJECT, f"ar@{domain}")
    documents = [req_doc]
    sightings = []
    for i, spec in enumerate(numbers):
        received = request_at - dt.timedelta(days=spec.lead_days)
        sender = _sender(spec, domain)
        thread = "req" if spec.in_thread else f"t{i}"
        subject = REQUEST_SUBJECT if spec.subject_in_context else f"Doc {i}"
        for channel in spec.channels:
            doc_id = f"d{i}-{channel}"
            documents.append(DocumentRef(doc_id, channel, received, sender, thread, subject, received))
            sightings.append(Sighting(spec.e164, doc_id, channel, received, spec.visibility, spec.role,
                                      spec.party, received))
    kwargs = dict(case_id=CASE, dataset_id=DATASET, vendor_id=VENDOR, vendor_domains=tuple(vendor_domains),
                  request=request, requested_cutoff=request_at, case_opened_at=opened,
                  documents=documents, sightings=sightings, settings=settings)
    poison = frozenset(s.e164 for s in numbers if s.is_poison)
    return World(kwargs=kwargs, poison=poison, settings=settings)


def run(world: World) -> dict:
    """Run the real selection rule on a world."""
    return select(**world.kwargs)


def _e164(n: int) -> str:
    return f"+1{n:010d}"


@st.composite
def _number_spec(draw, e164: str) -> NumberSpec:
    return NumberSpec(
        e164=e164,
        channels=draw(st.lists(st.sampled_from(CHANNELS), min_size=1, max_size=3, unique=True)),
        lead_days=draw(st.integers(min_value=0, max_value=800)),
        visibility=draw(st.sampled_from(VISIBILITIES)),
        role=draw(st.sampled_from(ROLES)),
        party=draw(st.sampled_from(PARTIES)),
        sender=draw(st.sampled_from(SENDERS)),
        in_thread=draw(st.booleans()),
        subject_in_context=draw(st.booleans()),
        is_poison=draw(st.booleans()),
    )


@st.composite
def worlds(draw, settings: Settings = DEFAULT) -> World:
    """Draw a world: 1 to 5 attacker-shaped numbers plus one plainly real, established vendor line,
    so the rule always has a legitimate pick to rank against."""
    count = draw(st.integers(min_value=1, max_value=5))
    seeds = draw(st.lists(st.integers(min_value=2015550000, max_value=2019559999),
                          min_size=count, max_size=count, unique=True))
    specs = [draw(_number_spec(_e164(s))) for s in seeds]
    real = _e164(2025550100)
    while real in {s.e164 for s in specs}:
        real = _e164(int(real[2:]) + 1)
    specs.append(NumberSpec(e164=real, channels=["contract", "listing"], lead_days=500,
                            visibility="visible", role="ar", party="vendor", sender="own",
                            in_thread=False, subject_in_context=False, near_miss_of=None, is_poison=False))
    return make_world(numbers=specs, settings=settings)
