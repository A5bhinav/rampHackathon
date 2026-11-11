"""Spec 0052: generated documents with a known truth for every phone number.

Code draws a plan, then renders it (decision 1). The plan is the truth:

- a `DocPlan` is one document: its kind, its author, its custody, filler lines, an optional quoted
  earlier message (`QuotePlan`, with its own author) and its numbers;
- a `NumberPlan` is one number: who really owns it, what the text claims about it (a role and a party,
  either of which may be absent), and how it is written: phrasing, placement, format and visibility,
  and whether a second number shares its line;
- a `WorldPlan` is a set of documents for one change request, for the end-to-end check (G7).

For the reader, the truth is the claim; for `select()`, it is the owner. They differ on purpose: an
attacker's text can claim its own number is the vendor's.

Plans come from a seeded `random.Random`, never Hypothesis, so a seed gives the same documents in any
process (decision 7; FINDINGS B10). A rendered document is the corpus's page model, the exact shape
`ingest()` takes: spans with position, size and colour, hidden by colour, size or position as
`visibility.py` classifies them (decision 3).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import random
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import phonenumbers as pn

from .docbank import (
    AREA_CODES,
    AUTHORS,
    CHANNEL,
    CLOCK,
    FILLER,
    FORMATS,
    KINDS_BY_AUTHOR,
    ORG,
    OWNERS,
    PHRASINGS,
    PLACEMENT_FORMS,
    PLACEMENTS,
    REQUEST_FILLER,
    SENDER,
    SIGNER,
    SUBJECT,
    TITLE,
    VISIBILITIES,
    WORLD_AREA_CODES,
    claim,
    write_number,
)
from .worldgen import fictional

EXTRACTOR = "lab-0052"
T0 = dt.datetime(2026, 10, 1, 16, 0, tzinfo=dt.UTC)   # every change request arrives at T0
WIDTH, HEIGHT = 612, 792
HIDE = {"white": {"color": "#ffffff"}, "tiny": {"size": 1}, "off_page": {"x": WIDTH + 90}}
VENDOR_DOMAIN = "acme.example"
FILE = "case.json"
VISIBILITY_WEIGHTS = (7, 1, 1, 1)                      # visible, white, tiny, off_page
# The vendor's own records in a G7 world use phrasings the stub reads as the vendor's, on one line.
VENDOR_RECORD_PHRASINGS = ("main", "accounts_receivable", "billing", "main_office", "ar_direct")
RECORD_KIND = {"contract": "contract_page", "listing": "listing_snapshot", "vendor_portal": "vendor_form",
               "paper_mail": "invoice"}


@dataclasses.dataclass(frozen=True)
class NumberPlan:
    e164: str
    owner: str                 # who really owns it: vendor | bank | customer | attacker | unrelated
    role: str | None           # the claim's role: main | ar | billing | fax, or None
    party: str | None          # the claim's party: vendor | bank | customer, or None
    phrasing: str              # a key of docbank.PHRASINGS
    placement: str             # one of docbank.PLACEMENTS
    format: str                # a key of docbank.FORMATS
    visibility: str            # visible | white | tiny | off_page
    shares_line: bool = False  # flagged numbers come in consecutive pairs, each pair on one line


@dataclasses.dataclass(frozen=True)
class QuotePlan:
    author: str
    sender: str
    filler: tuple[str, ...]
    numbers: tuple[NumberPlan, ...]


@dataclasses.dataclass(frozen=True)
class DocPlan:
    id: str
    kind: str
    author: str
    channel: str
    sender: str | None
    thread: str | None
    subject: str | None
    received_at: str
    filler: tuple[str, ...]
    numbers: tuple[NumberPlan, ...]
    quote: QuotePlan | None = None


@dataclasses.dataclass(frozen=True)
class WorldPlan:
    id: str
    planted: bool              # a planted provable number is in the vendor's records
    real: str                  # the vendor's real number
    request: str               # the change request's document id
    vendor_domains: tuple[str, ...]
    requested_cutoff: str
    case_opened_at: str
    documents: tuple[DocPlan, ...]


Plan = DocPlan | WorldPlan


# --- Reading a plan ----------------------------------------------------------------------------------

def numbers_with_authors(plan: DocPlan) -> list[tuple[str, NumberPlan]]:
    """Every number in a document, with the author whose words claim it (a quoted number's is the
    quoted author)."""
    out = [(plan.author, n) for n in plan.numbers]
    if plan.quote is not None:
        out += [(plan.quote.author, n) for n in plan.quote.numbers]
    return out


def documents_of(plan: Plan) -> tuple[DocPlan, ...]:
    return plan.documents if isinstance(plan, WorldPlan) else (plan,)


def all_numbers(plan: Plan) -> list[str]:
    return [n.e164 for d in documents_of(plan) for _, n in numbers_with_authors(d)]


# --- Rendering ---------------------------------------------------------------------------------------

def _groups(numbers: Iterable[NumberPlan]) -> list[tuple[NumberPlan, ...]]:
    """The numbers in line groups: a flagged pair shares one line, every other number has its own block."""
    numbers, out, i = list(numbers), [], 0
    while i < len(numbers):
        n = numbers[i]
        if n.shares_line:
            if i + 1 >= len(numbers) or not numbers[i + 1].shares_line:
                raise ValueError(f"{n.e164} shares its line with no partner: flagged numbers come in pairs")
            out.append((n, numbers[i + 1]))
            i += 2
        else:
            out.append((n,))
            i += 1
    return out


def _inline(n: NumberPlan) -> str:
    p = PHRASINGS[n.phrasing]
    return f"{p.text}: {write_number(n.e164, n.format)}"


def _rows(group: tuple[NumberPlan, ...], author: str) -> list[list[tuple[str, int]]]:
    """The visual lines of one number (or one shared line), each a list of (text, x) spans."""
    if len(group) == 2:
        return [[("   ".join(_inline(n) for n in group), 72)]]
    (n,) = group
    p = PHRASINGS[n.phrasing]
    words, number = p.text, write_number(n.e164, n.format)
    cap = words[0].upper() + words[1:]
    if n.placement == "label_number":
        return [[(f"{words}: {number}" if p.form == "label" else f"{cap} {number}", 72)]]
    if n.placement == "number_label":
        return [[(f"{number} ({words})", 72)]]
    if n.placement == "label_above":
        return [[(f"{cap}:", 72)], [(number, 72)]]
    if n.placement == "table_row":
        return [[(cap, 72), (number, 320)]]
    if n.placement == "signature":
        return [[("--", 72)], [(SIGNER[author], 72)], [(ORG[author], 72)], [(f"{words} - {number}", 72)]]
    if n.placement == "sentence":
        text = {"label": f"For questions, {words} is {number}.", "lead": f"{cap} {number} with any questions.",
                "mention": f"Please stop using {words} {number}."}[p.form]
        return [[(text, 72)]]
    if n.placement == "clock_time":
        return [[(f"{CLOCK} {words}: {number}" if p.form == "label" else f"{CLOCK} {words} {number}", 72)]]
    raise ValueError(f"unknown placement {n.placement!r}")


def number_line(n: NumberPlan, plan: DocPlan) -> str:
    """The text of the line that holds `n`, alone (what `stub_readable` gives the stub)."""
    for author, numbers, prefix in _parts(plan):
        for group in _groups(numbers):
            if n in group:
                rows = _rows(group, author)
                for row in rows:
                    for k, (text, _) in enumerate(row):
                        if write_number(n.e164, n.format) in text:
                            return (prefix if k == 0 else "") + text
    raise ValueError(f"{n.e164} is not in {plan.id}")


def _parts(plan: DocPlan) -> list[tuple[str, tuple[NumberPlan, ...], str]]:
    parts = [(plan.author, plan.numbers, "")]
    if plan.quote is not None:
        parts.append((plan.quote.author, plan.quote.numbers, "> "))
    return parts


def _body(filler: tuple[str, ...], numbers: tuple[NumberPlan, ...], author: str, prefix: str):
    groups, out = _groups(numbers), []
    for i in range(max(len(filler), len(groups))):
        if i < len(filler):
            out.append(([(prefix + filler[i], 72)], "visible"))
        if i < len(groups):
            for row in _rows(groups[i], author):
                (first, x), *rest = row
                out.append(([(prefix + first, x), *rest], groups[i][0].visibility))
    return out


def render(plan: DocPlan) -> dict:
    """The document a plan describes, in the page model ingest() reads. A hidden number's whole block is
    hidden; filler, title and quote header lines are visible."""
    rows = [([(TITLE[plan.kind], 72)], "visible"), ([(ORG[plan.author], 72)], "visible")]
    rows += _body(plan.filler, plan.numbers, plan.author, "")
    if plan.quote is not None:
        rows.append(([(f"On 2026-09-14, {plan.quote.sender} wrote:", 72)], "visible"))
        rows += _body(plan.quote.filler, plan.quote.numbers, plan.quote.author, "> ")
    spans = []
    for k, (row, visibility) in enumerate(rows):
        for text, x in row:
            spans.append({"text": text, "x": x, "y": 72 + 14 * k, "size": 10, "color": "#000000"}
                         | HIDE.get(visibility, {}))
    if 72 + 14 * len(rows) >= HEIGHT:
        raise ValueError(f"{plan.id} runs off its page")
    return {"id": plan.id, "kind": plan.kind,
            "custody": {"channel": plan.channel, "received_at": plan.received_at, "sender": plan.sender,
                        "thread_id": plan.thread, "subject": plan.subject, "parent_document_id": None},
            "pages": [{"width": WIDTH, "height": HEIGHT, "spans": spans}]}


def rendered(plan: Plan) -> list[dict]:
    return [render(d) for d in documents_of(plan)]


# --- Drawing -----------------------------------------------------------------------------------------

def _fresh(rng: random.Random, used: set[str], areas=AREA_CODES) -> str:
    while True:
        e164 = f"+1{rng.choice(areas)}55501{rng.randrange(100):02d}"
        if e164 not in used:
            used.add(e164)
            return e164


def _owner(rng: random.Random, author: str, phrasing: str) -> str:
    named = PHRASINGS[phrasing].party
    if author == "attacker":   # its own number, or the vendor's real one called something else (A1)
        return rng.choice(("attacker", "vendor") if named in (None, "vendor") else (named, "vendor"))
    if named is not None:
        return named
    return author


def _number(rng: random.Random, e164: str, owner: str | None, author: str) -> NumberPlan:
    phrasing = rng.choice(list(PHRASINGS))
    form = PHRASINGS[phrasing].form
    placement = rng.choice([p for p in PLACEMENTS if form in PLACEMENT_FORMS[p]])
    role, party = claim(phrasing, author)
    return NumberPlan(e164, owner if owner is not None else _owner(rng, author, phrasing), role, party, phrasing,
                      placement, rng.choice(list(FORMATS)), rng.choices(VISIBILITIES, weights=VISIBILITY_WEIGHTS)[0])


def _pairs(rng: random.Random, numbers: list[NumberPlan]) -> tuple[NumberPlan, ...]:
    """Sometimes put two neighbouring label-form numbers on one line (spec 0049's hard case)."""
    i = 0
    while i + 1 < len(numbers):
        a, b = numbers[i], numbers[i + 1]
        if rng.random() < 0.3 and PHRASINGS[a.phrasing].form == PHRASINGS[b.phrasing].form == "label":
            numbers[i] = dataclasses.replace(a, placement="label_number", shares_line=True)
            numbers[i + 1] = dataclasses.replace(b, placement="label_number", shares_line=True, visibility=a.visibility)
            i += 2
        else:
            i += 1
    return tuple(numbers)


def _draw_doc(rng: random.Random, doc_id: str) -> DocPlan:
    author = rng.choice(AUTHORS)
    kind = rng.choice(KINDS_BY_AUTHOR[author])
    channel = CHANNEL[kind]
    email = channel == "email"
    used: set[str] = set()
    numbers = _pairs(rng, [_number(rng, _fresh(rng, used), None, author) for _ in range(rng.randint(1, 4))])
    quote = None
    if email and rng.random() < 0.5:
        quoted = rng.choice(AUTHORS)
        quote = QuotePlan(quoted, SENDER[quoted], tuple(rng.sample(FILLER, rng.randint(0, 1))),
                          _pairs(rng, [_number(rng, _fresh(rng, used), None, quoted) for _ in range(rng.randint(1, 2))]))
    filler = tuple(rng.sample(FILLER, rng.randint(1, 3)))
    received = (T0 - dt.timedelta(days=rng.randint(1, 400))).isoformat()
    return DocPlan(doc_id, kind, author, channel, SENDER[author] if email else None, f"t-{doc_id}" if email else None,
                   SUBJECT.get(kind) if email else None, received, filler, numbers, quote)


def draw_docs(*, examples: int, seed: int) -> list[DocPlan]:
    """`examples` document plans at `seed`. A smaller sample is the start of a larger one."""
    rng = random.Random(f"dialback-docgen:docs:{seed}")
    return [_draw_doc(rng, f"doc-s{seed}-{i:05d}") for i in range(examples)]


def layouts() -> list[tuple[str, str]]:
    """Every phrasing and placement the generator can render together (102 today)."""
    return [(p, pl) for p in PHRASINGS for pl in PLACEMENTS if PHRASINGS[p].form in PLACEMENT_FORMS[pl]]


# The paths a change-context mention can take: the request email itself, a reply in its thread, a
# follow-up after it in a thread of its own, and an email from a lookalike sender the day before.
PATHS = ("request", "reply", "after", "lookalike")


def _draw_world(rng: random.Random, world_id: str, planted: bool, phrasing: str, placement: str) -> WorldPlan:
    """One G7 world: the vendor's own records, a planted provable number or not, and change-context text
    that mentions the vendor's real number once, visibly, as `phrasing` in `placement`. The request also
    names a fresh attacker number (first seen there, so never callable). Everything else is drawn."""
    real_area, planted_area, fresh_area = rng.sample(WORLD_AREA_CODES, 3)
    real, planted_number, fresh = (f"+1{a}55501{rng.randrange(100):02d}" for a in (real_area, planted_area, fresh_area))
    days = lambda n: dt.timedelta(days=n)
    docs = []
    for k, channel in enumerate(rng.sample(sorted(RECORD_KIND), rng.randint(2, 4))):   # the vendor's own records
        record = rng.choice(VENDOR_RECORD_PHRASINGS)
        n = NumberPlan(real, "vendor", *claim(record, "vendor"), record, "label_number", rng.choice(list(FORMATS)),
                       "visible")
        docs.append(DocPlan(f"{world_id}-rec{k}", RECORD_KIND[channel], "vendor", channel, None, None, None,
                            (T0 - days(rng.randint(200, 600))).isoformat(), (rng.choice(FILLER),), (n,)))
    if planted:   # a provable number: 2 counting channels, at least 90 days old, labeled the vendor's
        lead = rng.randint(100, 180)
        for k, channel in enumerate(("contract", "listing")):
            n = NumberPlan(planted_number, "attacker", *claim("accounts_receivable", "attacker"), "accounts_receivable",
                           "label_number", rng.choice(list(FORMATS)), "visible")
            docs.append(DocPlan(f"{world_id}-plant{k}", RECORD_KIND[channel], "attacker", channel, None, None, None,
                                (T0 - days(lead)).isoformat(), (rng.choice(FILLER),), (n,)))
    mention = NumberPlan(real, "vendor", *claim(phrasing, "attacker"), phrasing, placement, rng.choice(list(FORMATS)),
                         "visible")
    path = rng.choice(PATHS)
    request_numbers = [_number(rng, fresh, "attacker", "attacker")] + ([mention] if path == "request" else [])
    rng.shuffle(request_numbers)
    request = f"{world_id}-req"
    docs.append(DocPlan(request, "change_request_email", "attacker", "email",
                        rng.choice(("ar@acme.example", "ar@acrne.example")), "req", SUBJECT["change_request_email"],
                        T0.isoformat(), tuple(rng.sample(REQUEST_FILLER, 2)), tuple(request_numbers)))
    if path != "request":   # the mention's own email, in the change context by that path
        sender, thread, when, subject = {
            "reply": ("ar@other.example", "req", T0 + dt.timedelta(minutes=10), "Re: Updated banking details"),
            "after": ("ar@acme.example", f"{world_id}-t0", T0 + dt.timedelta(minutes=20), "One more thing"),
            "lookalike": ("ar@acrne.example", f"{world_id}-t0", T0 - days(1), "Payment contact"),
        }[path]
        docs.append(DocPlan(f"{world_id}-f0", "email_body", "attacker", "email", sender, thread, subject,
                            when.isoformat(), (rng.choice(FILLER),), (mention,)))
    return WorldPlan(world_id, planted, real, request, (VENDOR_DOMAIN,), T0.isoformat(),
                     (T0 + dt.timedelta(hours=1)).isoformat(), tuple(docs))


def draw_worlds(*, seed: int) -> list[WorldPlan]:
    """G7's sweep (decision 5): for every phrasing and placement, one world with a planted provable number
    and one without, whose change context mentions the real number in that layout; so exactly half the
    worlds are planted, and which layouts fail G7 doesn't depend on the seed. The rest is drawn at `seed`."""
    rng = random.Random(f"dialback-docgen:worlds:{seed}")
    out: list[WorldPlan] = []
    for phrasing, placement in layouts():
        for planted in (True, False):
            out.append(_draw_world(rng, f"world-s{seed}-{len(out):04d}", planted, phrasing, placement))
    return out


# --- Fictional numbers ------------------------------------------------------------------------------

def real_looking(texts: Iterable[str]) -> list[str]:
    """Every phone number phonenumbers finds in `texts` (leniently, so more than ingestion's strict
    matcher) that is outside the block reserved for fiction, as E.164, in order of first appearance."""
    found: list[str] = []
    for text in texts:
        for m in pn.PhoneNumberMatcher(text, "US", leniency=pn.Leniency.POSSIBLE):
            e164 = pn.format_number(m.number, pn.PhoneNumberFormat.E164)
            if not fictional(e164) and e164 not in found:
                found.append(e164)
    return found


# --- The frozen JSON ---------------------------------------------------------------------------------

def plan_to_json(plan: Plan) -> dict:
    return {"type": "world" if isinstance(plan, WorldPlan) else "doc", **dataclasses.asdict(plan)}


def _number_from(d: dict) -> NumberPlan:
    return NumberPlan(**d)


def _doc_from(d: dict) -> DocPlan:
    quote = d["quote"]
    return DocPlan(**{**d, "filler": tuple(d["filler"]), "numbers": tuple(_number_from(n) for n in d["numbers"]),
                      "quote": None if quote is None else QuotePlan(
                          quote["author"], quote["sender"], tuple(quote["filler"]),
                          tuple(_number_from(n) for n in quote["numbers"]))})


def plan_from_json(data: dict) -> Plan:
    body = {k: v for k, v in data.items() if k != "type"}
    if data["type"] == "world":
        return WorldPlan(**{**body, "vendor_domains": tuple(body["vendor_domains"]),
                            "documents": tuple(_doc_from(d) for d in body["documents"])})
    return _doc_from(body)


def _normal(value) -> object:
    return json.loads(json.dumps(value))


def freeze_case(plan: Plan, name: str, out_dir: Path, *, patterns: Iterable[str] = (), why: str = "") -> Path:
    """Write the plan and its rendered documents as `<out_dir>/<name>/case.json`. Refuses a real-looking
    number, and never replaces a frozen case with a different plan; freezing the same plan again leaves
    the case as it is (its patterns record what failed when it was frozen)."""
    texts = [span["text"] for d in rendered(plan) for page in d["pages"] for span in page["spans"]]
    if bad := sorted({n for n in all_numbers(plan) if not fictional(n)} | set(real_looking(texts))):
        raise ValueError(f"refusing to freeze real-looking numbers {bad}: the lab writes only NPA-555-0100 to 0199")
    case = Path(out_dir) / name
    body = {"name": name, "patterns": list(patterns), "why": why, "plan": plan_to_json(plan),
            "documents": rendered(plan)}
    if (case / FILE).is_file():
        existing = json.loads((case / FILE).read_text(encoding="utf-8"))
        if existing.get("plan") != _normal(body["plan"]):
            raise FileExistsError(f"{case} already holds a different plan; a frozen case is never replaced")
        return case
    case.mkdir(parents=True, exist_ok=True)
    (case / FILE).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return case


def load_case(case_dir: Path) -> tuple[Plan, list[dict], dict]:
    """A frozen case: its plan, its documents as frozen, and the whole record."""
    record = json.loads((Path(case_dir) / FILE).read_text(encoding="utf-8"))
    return plan_from_json(record["plan"]), record["documents"], record


# --- Coverage ----------------------------------------------------------------------------------------

# Stated floors over a 1000-document sample (spec 0052, acceptance): numbers per phrasing, placement,
# format, visibility and owner; documents per author; quoted numbers; numbers on a shared line.
FLOORS = {"phrasing": 50, "placement": 100, "format": 200, "visibility": 100, "author": 100, "owner": 100,
          "quoted": 50, "shared_line": 50}
VALUES = {"phrasing": tuple(PHRASINGS), "placement": PLACEMENTS, "format": tuple(FORMATS), "visibility": VISIBILITIES,
          "author": AUTHORS, "owner": OWNERS, "quoted": ("numbers",), "shared_line": ("numbers",)}


def coverage(plans: Iterable[DocPlan]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {dim: Counter() for dim in FLOORS}
    for plan in plans:
        counts["author"][plan.author] += 1
        for _, n in numbers_with_authors(plan):
            for dim in ("phrasing", "placement", "format", "visibility", "owner"):
                counts[dim][getattr(n, dim)] += 1
            counts["shared_line"]["numbers"] += n.shares_line
        if plan.quote is not None:
            counts["quoted"]["numbers"] += len(plan.quote.numbers)
    return counts


def below_floor(plans: Iterable[DocPlan]) -> list[str]:
    """Every value under its stated floor, as `<dimension> <value>: <count> < <floor>`."""
    counts = coverage(plans)
    return [f"{dim} {value}: {counts[dim][value]} < {floor}" for dim, floor in FLOORS.items()
            for value in VALUES[dim] if counts[dim][value] < floor]


# --- The checks (decision 5) -------------------------------------------------------------------------
#
# G1 to G6 judge one document through real ingestion with a named reader; G7 judges a world end to end
# through ingestion and select(). Every one must hold for any reader. A violation is keyed by its pattern:
# the check, and the phrasing and placement of the number it is about (decision 6).

DOC_CHECKS = ("G1", "G2", "G3", "G4", "G5", "G6")
G7_MOVED = "I12_context_label_moved_call"          # the removal half of spec 0083's I12
G7_FIRST = "I1_first_seen_in_context_eligible"


def pattern_key(check: str, phrasing: str, placement: str) -> str:
    return f"{check}/{phrasing}/{placement}"


@dataclasses.dataclass(frozen=True)
class Violation:
    check: str
    phrasing: str
    placement: str
    plan: str          # the plan's id
    number: str        # the number it is about ("" when none)
    detail: str

    @property
    def pattern(self) -> str:
        return pattern_key(self.check, self.phrasing, self.placement)


@dataclasses.dataclass
class Tally:
    """What a run counts, beyond its violations (the stub report)."""
    documents: int = 0
    visible_numbers: int = 0                       # planned visible numbers that got a candidate
    unsafe: int = 0                                # G3
    wrong_party: int = 0                           # G4
    overblocks: Counter = dataclasses.field(default_factory=Counter)   # by "stub_readable" / "not_stub_readable"
    overblocks_fax: int = 0                        # of those, vendor fax lines (the rule refuses a fax anyway)
    vendor_role_claims: Counter = dataclasses.field(default_factory=Counter)   # the over-block denominators
    g5_compared: int = 0
    g5_same: int = 0
    g6_compared: int = 0
    worlds: int = 0
    worlds_planted: int = 0

    def add(self, other: Tally) -> None:
        for f in dataclasses.fields(self):
            mine, theirs = getattr(self, f.name), getattr(other, f.name)
            setattr(self, f.name, mine + theirs)


@dataclasses.dataclass
class Result:
    violations: list[Violation] = dataclasses.field(default_factory=list)
    tally: Tally = dataclasses.field(default_factory=Tally)

    def add(self, other: Result) -> None:
        self.violations += other.violations
        self.tally.add(other.tally)


def _observe(plan: DocPlan, reader, ingest) -> tuple[dict[str, dict], list[dict]]:
    """Ingest one document; its observations by number, and any observation of a number the plan lacks."""
    result = ingest([render(plan)], extractor_version=EXTRACTOR, reader=reader)
    planned = {n.e164 for _, n in numbers_with_authors(plan)}
    by_number: dict[str, dict] = {}
    extra = []
    for o in result.observations:
        if o["document_id"] != plan.id:
            continue
        if o["value_normalized"] in planned and o["value_normalized"] not in by_number:
            by_number[o["value_normalized"]] = o
        else:
            extra.append(o)
    return by_number, extra


def stub_readable(n: NumberPlan, plan: DocPlan) -> bool:
    """Decision 2: the stub, given the number's line alone, returns its claimed role and party."""
    from dialback_ledger.reader import StubReader
    labels = StubReader().label(number_line(n, plan))
    return len(labels) == 1 and (labels[0]["role"], labels[0]["party"]) == (n.role or "unknown", n.party or "unknown")


def _replace_number(plan: DocPlan, old: NumberPlan, new: NumberPlan) -> DocPlan:
    if old in plan.numbers:
        return dataclasses.replace(plan, numbers=tuple(new if n == old else n for n in plan.numbers))
    quote = plan.quote
    return dataclasses.replace(plan, quote=dataclasses.replace(quote, numbers=tuple(new if n == old else n
                                                                                     for n in quote.numbers)))


def _pick(options: list[str], *key: str) -> str:
    """One of `options`, chosen by a stable hash of `key`: the same plan always gets the same variant."""
    import hashlib
    return options[int(hashlib.sha256("|".join(key).encode()).hexdigest(), 16) % len(options)]


def variants(plan: DocPlan, n: NumberPlan, author: str) -> list[DocPlan]:
    """G5's rewordings of one number: another phrasing of the same claim, another format, another
    placement, one at a time, each chosen by a stable hash so a plan always gets the same ones."""
    form = PHRASINGS[n.phrasing].form
    out = []
    same = [k for k in PHRASINGS if k != n.phrasing and claim(k, author) == (n.role, n.party)
            and (PHRASINGS[k].form == "label" if n.shares_line else PHRASINGS[k].form in PLACEMENT_FORMS[n.placement])]
    if same:
        out.append(dataclasses.replace(n, phrasing=_pick(same, plan.id, n.e164, "phrasing")))
    out.append(dataclasses.replace(n, format=_pick([f for f in FORMATS if f != n.format], plan.id, n.e164, "format")))
    if not n.shares_line:
        places = [p for p in PLACEMENTS if p != n.placement and form in PLACEMENT_FORMS[p]]
        out.append(dataclasses.replace(n, placement=_pick(places, plan.id, n.e164, "placement")))
    return [_replace_number(plan, n, v) for v in out]


def _wrong(party: str, claimed: str | None) -> bool:
    """A known label party the claim doesn't support."""
    return party != "unknown" and party != claimed


def check_doc(plan: DocPlan, *, reader, ingest=None, checks: Iterable[str] = DOC_CHECKS) -> Result:
    """G1 to G6 on one document plan, through `ingest` (the ledger's real ingest_all by default) with `reader`."""
    if ingest is None:
        from dialback_ledger.ingest import ingest_all as ingest
    checks, res = set(checks), Result()
    res.tally.documents = 1
    seen, extra = _observe(plan, reader, ingest)
    numbered = numbers_with_authors(plan)
    by_number = {n.e164: n for _, n in numbered}

    def v(check: str, n: NumberPlan | None, detail: str) -> None:
        res.violations.append(Violation(check, n.phrasing if n else "unplanned", n.placement if n else "filler",
                                        plan.id, n.e164 if n else "", detail))

    if "G1" in checks:    # extraction: every planned number found once, with its planned visibility, nothing else
        for _, n in numbered:
            o = seen.get(n.e164)
            if o is None:
                v("G1", n, f"{n.e164} ({n.format}, {n.visibility}) is not a candidate")
            elif o["visibility"] != n.visibility:
                v("G1", n, f"{n.e164} is {o['visibility']}, planned {n.visibility}")
        for o in extra:
            v("G1", by_number.get(o["value_normalized"]), f"unplanned candidate {o['value_normalized']}")
    if "G2" in checks:    # hidden text is never labeled
        for o in [*seen.values(), *extra]:
            if o["visibility"] != "visible" and (o["role"], o["party"]) != ("unknown", "unknown"):
                v("G2", by_number.get(o["value_normalized"]),
                  f"hidden ({o['visibility']}) {o['value_normalized']} labeled {o['role']}, {o['party']}")
    for author, n in numbered:
        o = seen.get(n.e164)
        if n.visibility != "visible" or o is None:
            continue
        res.tally.visible_numbers += 1
        if "G3" in checks and n.party != "vendor" and o["party"] == "vendor":
            res.tally.unsafe += 1
            v("G3", n, f"claim {n.role}, {n.party} labeled {o['role']}, vendor")
        if "G4" in checks and n.party is not None and _wrong(o["party"], n.party):
            res.tally.wrong_party += 1
            v("G4", n, f"claim {n.role}, {n.party} labeled {o['role']}, {o['party']}")
        if "G4" in checks and n.party == "vendor" and n.role is not None:
            split = "stub_readable" if stub_readable(n, plan) else "not_stub_readable"
            res.tally.vendor_role_claims[split] += 1
            if o["party"] == "unknown":
                res.tally.overblocks[split] += 1
                res.tally.overblocks_fax += n.role == "fax"
        if "G5" in checks:
            for variant in variants(plan, n, author):
                moved = next(m for _, m in numbers_with_authors(variant) if m.e164 == n.e164)
                ov = _observe(variant, reader, ingest)[0].get(n.e164)
                if ov is None:
                    continue
                res.tally.g5_compared += 1
                res.tally.g5_same += (ov["role"], ov["party"]) == (o["role"], o["party"])
                if "unknown" not in (o["party"], ov["party"]) and o["party"] != ov["party"]:
                    side = n if _wrong(o["party"], n.party) else moved
                    res.violations.append(Violation(
                        "G5", side.phrasing, side.placement, plan.id, n.e164,
                        f"{n.phrasing}/{n.placement}/{n.format} labeled {o['party']}, "
                        f"{moved.phrasing}/{moved.placement}/{moved.format} labeled {ov['party']}"))
    if "G6" in checks and plan.quote is not None:   # a quoted message never changes the original numbers' labels
        alone = _observe(dataclasses.replace(plan, quote=None), reader, ingest)[0]
        for n in plan.numbers:
            o, o0 = seen.get(n.e164), alone.get(n.e164)
            if o is None or o0 is None:
                continue
            res.tally.g6_compared += 1
            if (o["role"], o["party"]) != (o0["role"], o0["party"]):
                v("G6", n, f"labeled {o0['role']}, {o0['party']} alone, {o['role']}, {o['party']} under the quote")
    return res


def check_docs(plans: Iterable[DocPlan], *, reader, ingest=None, checks: Iterable[str] = DOC_CHECKS) -> Result:
    total, checks = Result(), tuple(checks)
    for plan in plans:
        total.add(check_doc(plan, reader=reader, ingest=ingest, checks=checks))
    return total


# --- G7: end to end ----------------------------------------------------------------------------------

def world_of(plan: WorldPlan, *, reader):
    """The select() world real ingestion makes of a world plan (the bridge, decision 4)."""
    from .bridge import world_from_documents
    poison = frozenset(n.e164 for d in plan.documents for _, n in numbers_with_authors(d) if n.owner == "attacker")
    return world_from_documents(rendered(plan), request_id=plan.request, vendor_domains=plan.vendor_domains,
                                requested_cutoff=dt.datetime.fromisoformat(plan.requested_cutoff),
                                case_opened_at=dt.datetime.fromisoformat(plan.case_opened_at), reader=reader,
                                extractor_version=EXTRACTOR, poison=poison)


def run_rule(world, rule=None) -> dict:
    from .invariants import _runner
    return _runner(rule)(world)


def _calls(out: dict) -> list[str]:
    from .invariants import _calls
    return _calls(out)


def _relabeled(world, keep, rule=None):
    """The world with the change-context sightings `keep` selects relabeled the vendor's main line."""
    from dialback_ledger.select import change_context_documents

    from .invariants import _with
    context = change_context_documents if rule is None else rule.change_context_documents
    ctx = {d.id for d in context(**world.kwargs)}
    return _with(world, sightings=[dataclasses.replace(s, party="vendor", role="main")
                                   if s.document_id in ctx and keep(s) else s for s in world.kwargs["sightings"]])


def g7(world, rule=None) -> list[str]:
    """G7 on one select() world: relabeling every change-context sighting ingestion produced as the
    vendor's must not change the call list (spec 0083's I12, removal half only: a label may trim it, as
    I12 allows), and no number first seen in the change context is eligible (I1)."""
    from .invariants import _frame
    base, found = run_rule(world, rule), set()
    if base.get("e164") is not None:
        calls = _calls(base)
        if calls != _calls(run_rule(_relabeled(world, lambda s: True, rule), rule))[:len(calls)]:
            found.add(G7_MOVED)
    f = _frame(world)
    for c in base.get("candidates", []):
        if c.get("eligible") and (n := f.numbers.get(c["e164"])) is not None and n.first_seen_in_context:
            found.add(G7_FIRST)
    return sorted(found)


def without_context_numbers(plan: WorldPlan) -> WorldPlan:
    """The world with no number in the change request or its follow-ups (a generator self-check)."""
    keep = {d.id for d in plan.documents if d.id.split("-")[-1].startswith(("rec", "plant"))}
    return dataclasses.replace(plan, documents=tuple(d if d.id in keep else dataclasses.replace(d, numbers=())
                                                     for d in plan.documents))


def check_world(plan: WorldPlan, *, reader, rule=None) -> list[Violation]:
    """G7 on one world plan, each failure keyed by the change-context number that caused it: every sighting
    whose label alone moves the call when relabeled, or, if none does alone, a minimal set of them whose
    labels together do (so a label that moves nothing is never blamed); for I1, the number's first
    change-context mention."""
    from dialback_ledger.select import change_context_documents

    from .invariants import _frame
    world = world_of(plan, reader=reader)
    labels = g7(world, rule)
    if not labels:
        return []
    docs = {d.id: d for d in plan.documents}

    def number(doc_id: str, e164: str) -> NumberPlan:
        return next(n for _, n in numbers_with_authors(docs[doc_id]) if n.e164 == e164)

    out = []
    if G7_MOVED in labels:
        base = _calls(run_rule(world, rule))
        context = change_context_documents if rule is None else rule.change_context_documents
        ctx = {d.id for d in context(**world.kwargs)}
        suspects = [s for s in world.kwargs["sightings"] if s.document_id in ctx and s.visibility == "visible"
                    and (s.party != "vendor" or str(s.role).strip().lower() == "fax")]
        def moves(chosen: list) -> bool:
            return base != _calls(run_rule(_relabeled(world, lambda r: r in chosen, rule), rule))[:len(base)]

        culprits = [s for s in suspects if moves([s])]
        if not culprits:   # no label moves it alone: a minimal set of them that still does, in sighting order
            culprits = list(suspects)
            for s in suspects:
                if moves([r for r in culprits if r != s]):
                    culprits.remove(s)
        for s in culprits:
            n = number(s.document_id, s.e164)
            out.append(Violation("G7", n.phrasing, n.placement, plan.id, s.e164,
                                 f"{G7_MOVED}: {s.document_id} labels it {s.role}, {s.party}; calls {base}"))
    if G7_FIRST in labels:
        f = _frame(world)
        for c in run_rule(world, rule).get("candidates", []):
            if c.get("eligible") and (seen := f.numbers.get(c["e164"])) is not None and seen.first_seen_in_context:
                first = min((s for s in seen.visible if s.document_id in f.in_context_ids),
                            key=lambda s: (f.docs[s.document_id].received_at, s.document_id))
                n = number(first.document_id, first.e164)
                out.append(Violation("G7", n.phrasing, n.placement, plan.id, first.e164,
                                     f"{G7_FIRST}: {first.e164} first seen in {first.document_id}"))
    return out


# --- A run: the fixed sample, shrinking, frozen cases, the report (decision 7) -----------------------

@dataclasses.dataclass
class Run:
    examples: int
    seed: int
    result: Result                       # G1 to G6 over the documents, with the tally
    world_violations: list[Violation]    # G7 over the worlds
    plans: dict[str, Plan]               # every plan in the sample, by id

    @property
    def violations(self) -> list[Violation]:
        return [*self.result.violations, *self.world_violations]


def run(*, examples: int, seed: int, reader, rule=None) -> Run:
    """Check every plan in the seed's fixed sample: `examples` documents with G1 to G6, and G7's sweep of
    worlds (two per layout). Nothing stops at the first failure (FINDINGS B3)."""
    docs = draw_docs(examples=examples, seed=seed)
    worlds = draw_worlds(seed=seed)
    result = check_docs(docs, reader=reader)
    found = [v for w in worlds for v in check_world(w, reader=reader, rule=rule)]
    result.tally.worlds, result.tally.worlds_planted = len(worlds), sum(w.planted for w in worlds)
    return Run(examples, seed, result, found, {p.id: p for p in [*docs, *worlds]})


def patterns_of(plan: Plan, *, reader, rule=None, checks: Iterable[str] | None = None) -> set[str]:
    if isinstance(plan, WorldPlan):
        return {v.pattern for v in check_world(plan, reader=reader, rule=rule)}
    return {v.pattern for v in check_doc(plan, reader=reader, checks=DOC_CHECKS if checks is None else checks).violations}


def _without(numbers: tuple[NumberPlan, ...], gone: NumberPlan) -> tuple[NumberPlan, ...]:
    """The numbers without `gone`; a partner left alone on its shared line gets a line of its own."""
    out = []
    for group in _groups(numbers):
        if gone in group:
            out += [dataclasses.replace(n, shares_line=False) for n in group if n != gone]
        else:
            out += group
    return tuple(out)


def _simpler_doc(plan: DocPlan):
    """Every one-step simplification of a document plan: remove a number or a filler line (in the quote
    too), or simplify a phrasing (to the first one making the same claim) or a format (to the first)."""
    for n in plan.numbers:
        yield dataclasses.replace(plan, numbers=_without(plan.numbers, n))
    for i in range(len(plan.filler)):
        yield dataclasses.replace(plan, filler=plan.filler[:i] + plan.filler[i + 1:])
    q = plan.quote
    if q is not None:
        for n in q.numbers:
            yield dataclasses.replace(plan, quote=dataclasses.replace(q, numbers=_without(q.numbers, n)))
        for i in range(len(q.filler)):
            yield dataclasses.replace(plan, quote=dataclasses.replace(q, filler=q.filler[:i] + q.filler[i + 1:]))
        if not q.numbers and not q.filler:      # an empty quote is only its header line
            yield dataclasses.replace(plan, quote=None)
    for author, n in numbers_with_authors(plan):
        form_ok = (lambda k: PHRASINGS[k].form == "label") if n.shares_line else \
            (lambda k, n=n: PHRASINGS[k].form in PLACEMENT_FORMS[n.placement])
        first = next(k for k in PHRASINGS if claim(k, author) == (n.role, n.party) and form_ok(k))
        if first != n.phrasing:
            yield _replace_number(plan, n, dataclasses.replace(n, phrasing=first))
        if n.format != next(iter(FORMATS)):
            yield _replace_number(plan, n, dataclasses.replace(n, format=next(iter(FORMATS))))


def simpler(plan: Plan):
    """Every one-step simplification of a plan. A world can also lose a document (never its request)."""
    if isinstance(plan, DocPlan):
        yield from _simpler_doc(plan)
        return
    for d in plan.documents:
        if d.id != plan.request:
            yield dataclasses.replace(plan, documents=tuple(x for x in plan.documents if x is not d))
    for k, d in enumerate(plan.documents):
        for smaller in _simpler_doc(d):
            yield dataclasses.replace(plan, documents=plan.documents[:k] + (smaller,) + plan.documents[k + 1:])


def shrink(plan: Plan, pattern: str, *, reader, rule=None) -> Plan:
    """Shrink a plan that shows `pattern` until every one-step simplification loses it (1-minimal)."""
    check = pattern.split("/", 1)[0]
    checks = None if check == "G7" else (check,)

    def still(p: Plan) -> bool:
        return pattern in patterns_of(p, reader=reader, rule=rule, checks=checks)

    if not still(plan):
        raise ValueError(f"{plan.id} does not show {pattern}")
    while True:
        for smaller in simpler(plan):
            if still(smaller):
                plan = smaller
                break
        else:
            return plan


def case_name(pattern: str, plan: Plan) -> str:
    """A frozen case's name: its pattern, and a digest of its plan, so re-finding it lands on the same case."""
    import hashlib
    digest = hashlib.sha256(json.dumps(plan_to_json(plan), sort_keys=True).encode()).hexdigest()[:8]
    return f"{pattern.lower().replace('/', '-').replace('_', '-')}-{digest}"


def rerun_frozen(out_dir: Path, *, reader, rule=None) -> list[tuple[str, list[str], bool]]:
    """Every frozen case under `out_dir`: its name, the patterns it shows now, and whether its plan no
    longer renders to its frozen documents (then it no longer means what it meant)."""
    out = []
    for case in sorted(Path(out_dir).glob(f"*/{FILE}")):
        plan, documents, _ = load_case(case.parent)
        out.append((case.parent.name, sorted(patterns_of(plan, reader=reader, rule=rule)),
                    _normal(rendered(plan)) != documents))
    return out


def freeze_new(found: Run, out_dir: Path, shown: set[str], *, reader, rule=None) -> list[tuple[str, str]]:
    """Shrink and freeze the first plan of every failing pattern no frozen case shows yet."""
    first: dict[str, str] = {}
    for v in found.violations:
        first.setdefault(v.pattern, v.plan)
    frozen, shown = [], set(shown)
    for pattern, plan_id in sorted(first.items()):
        if pattern in shown:
            continue
        small = shrink(found.plans[plan_id], pattern, reader=reader, rule=rule)
        patterns = sorted(patterns_of(small, reader=reader, rule=rule))
        name = case_name(pattern, small)
        freeze_case(small, name, out_dir, patterns=patterns,
                    why=f"{pattern}: shrunk from {plan_id}, found by lab docs at seed {found.seed} in {found.examples} "
                        "documents")
        shown |= set(patterns)
        frozen.append((name, pattern))
    return frozen


def report(found: Run) -> dict:
    """The stub report: unsafe labels, wrong parties, over-blocks split by stub-readable, G5's stability
    rate, and every failing pattern with its count."""
    t = found.result.tally
    readable = {split: {"left_unlabeled": t.overblocks[split], "vendor_claims_with_a_role": t.vendor_role_claims[split]}
                for split in ("stub_readable", "not_stub_readable")}
    return {
        "seed": found.seed, "documents": t.documents, "worlds": t.worlds, "worlds_planted": t.worlds_planted,
        "visible_numbers": t.visible_numbers,
        "unsafe_labels": t.unsafe, "wrong_parties": t.wrong_party,
        "overblocks": {**readable, "fax_lines_among_them": t.overblocks_fax},
        "g5_stability": {"same": t.g5_same, "compared": t.g5_compared,
                         "rate": t.g5_same / t.g5_compared if t.g5_compared else None},
        "g6_compared": t.g6_compared,
        "g7_failing_worlds": len({v.plan for v in found.world_violations}),
        "patterns": dict(sorted(Counter(v.pattern for v in found.violations).items())),
    }


def report_lines(body: dict) -> list[str]:
    o, g5 = body["overblocks"], body["g5_stability"]
    rate = "n/a" if g5["rate"] is None else f"{g5['rate']:.1%}"
    lines = [
        (f"{body['documents']} documents and {body['worlds']} worlds ({body['worlds_planted']} with a planted "
         f"number) at seed {body['seed']}"),
        f"unsafe labels (G3): {body['unsafe_labels']} of {body['visible_numbers']} visible numbers",
        f"wrong parties (G4): {body['wrong_parties']}",
        ("over-blocks (a vendor claim with a role, left unlabeled; not gated): "
         f"stub-readable {o['stub_readable']['left_unlabeled']} of {o['stub_readable']['vendor_claims_with_a_role']}, "
         f"not stub-readable {o['not_stub_readable']['left_unlabeled']} of "
         f"{o['not_stub_readable']['vendor_claims_with_a_role']} ({o['fax_lines_among_them']} of them fax lines, "
         "which the rule refuses anyway)"),
        f"G5 stability: the same label on {g5['same']} of {g5['compared']} rewordings ({rate})",
        f"G6: {body['g6_compared']} original numbers compared with and without their quote",
        f"G7: {body['g7_failing_worlds']} of {body['worlds']} worlds fail",
    ]
    return lines + [f"{pattern}: {count} violation(s)" for pattern, count in body["patterns"].items()]
