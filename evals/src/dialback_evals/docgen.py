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


def world_count(examples: int) -> int:
    """How many G7 worlds a run of `examples` documents draws: one for every ten."""
    return max(1, examples // 10)


def _mentions(rng: random.Random, pool: list[tuple[str, str]], count: int) -> tuple[NumberPlan, ...]:
    """Numbers in the attacker's change-context text, each a distinct number from `pool` with its owner."""
    return tuple(_number(rng, e164, owner, "attacker") for e164, owner in rng.sample(pool, min(count, len(pool))))


def _draw_world(rng: random.Random, world_id: str, planted: bool) -> WorldPlan:
    real_area, planted_area, fresh_area = rng.sample(WORLD_AREA_CODES, 3)
    real, planted_number, fresh = (f"+1{a}55501{rng.randrange(100):02d}" for a in (real_area, planted_area, fresh_area))
    days = lambda n: dt.timedelta(days=n)
    docs = []
    for k, channel in enumerate(rng.sample(sorted(RECORD_KIND), rng.randint(2, 4))):   # the vendor's own records
        phrasing = rng.choice(VENDOR_RECORD_PHRASINGS)
        n = NumberPlan(real, "vendor", *claim(phrasing, "vendor"), phrasing, "label_number", rng.choice(list(FORMATS)),
                       "visible")
        docs.append(DocPlan(f"{world_id}-rec{k}", RECORD_KIND[channel], "vendor", channel, None, None, None,
                            (T0 - days(rng.randint(200, 600))).isoformat(), (rng.choice(FILLER),), (n,)))
    pool = [(real, "vendor"), (fresh, "attacker")]
    if planted:   # a provable number: 2 counting channels, at least 90 days old, labeled the vendor's
        lead = rng.randint(100, 180)
        for k, channel in enumerate(("contract", "listing")):
            n = NumberPlan(planted_number, "attacker", *claim("accounts_receivable", "attacker"), "accounts_receivable",
                           "label_number", rng.choice(list(FORMATS)), "visible")
            docs.append(DocPlan(f"{world_id}-plant{k}", RECORD_KIND[channel], "attacker", channel, None, None, None,
                                (T0 - days(lead)).isoformat(), (rng.choice(FILLER),), (n,)))
        pool.append((planted_number, "attacker"))
    request = f"{world_id}-req"
    docs.append(DocPlan(request, "change_request_email", "attacker", "email",
                        rng.choice(("ar@acme.example", "ar@acrne.example")), "req", SUBJECT["change_request_email"],
                        T0.isoformat(), tuple(rng.sample(REQUEST_FILLER, 2)), _mentions(rng, pool, rng.randint(0, 3))))
    for k in range(rng.randint(0, 2)):   # follow-ups, each in the change context by one path
        sender, thread, when, subject = rng.choice((
            ("ar@other.example", "req", T0 + dt.timedelta(minutes=10), "Re: Updated banking details"),   # a reply
            ("ar@acme.example", f"{world_id}-t{k}", T0 + dt.timedelta(minutes=20), "One more thing"),  # after it
            ("ar@acrne.example", f"{world_id}-t{k}", T0 - days(1), "Payment contact"),                  # a lookalike
        ))
        docs.append(DocPlan(f"{world_id}-f{k}", "email_body", "attacker", "email", sender, thread, subject,
                            when.isoformat(), (rng.choice(FILLER),), _mentions(rng, pool, rng.randint(1, 2))))
    return WorldPlan(world_id, planted, real, request, (VENDOR_DOMAIN,), T0.isoformat(),
                     (T0 + dt.timedelta(hours=1)).isoformat(), tuple(docs))


def draw_worlds(*, count: int, seed: int) -> list[WorldPlan]:
    """`count` G7 world plans at `seed`; exactly half (the even ones) hold a planted provable number."""
    rng = random.Random(f"dialback-docgen:worlds:{seed}")
    return [_draw_world(rng, f"world-s{seed}-{i:04d}", planted=i % 2 == 0) for i in range(count)]


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
