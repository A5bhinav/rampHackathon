"""Spec 0052: generated documents with a known claim and owner for every phone number, run through real
ingestion (with a named reader) and select(), held to checks any reader must pass."""
from __future__ import annotations

import dataclasses
import json
from functools import lru_cache
from pathlib import Path

import pytest
from dialback_evals import docbank, docgen
from dialback_ledger.candidates import parse_phone
from dialback_ledger.ingest import ingest_all
from dialback_ledger.reader import StubReader

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "evals" / "docs-generated"
SEED, EXAMPLES = 0, 1000


@lru_cache(maxsize=1)
def _docs() -> tuple[docgen.DocPlan, ...]:
    return tuple(docgen.draw_docs(examples=EXAMPLES, seed=SEED))


@lru_cache(maxsize=1)
def _worlds() -> tuple[docgen.WorldPlan, ...]:
    return tuple(docgen.draw_worlds(count=docgen.world_count(EXAMPLES), seed=SEED))


# --- Shape, round trip, coverage ---------------------------------------------------------------------

def test_area_codes_parse_in_every_format():
    # Decision 2: area codes come only from a list checked to parse in all five formats; 756 parses in none.
    assert {"415", "469"} <= set(docbank.AREA_CODES) and "756" not in docbank.AREA_CODES
    for area in docbank.AREA_CODES:
        for last in (0, 42, 99):
            e164 = f"+1{area}55501{last:02d}"
            assert all(parse_phone(docbank.write_number(e164, f)) == [e164] for f in docbank.FORMATS), e164
    assert not any(parse_phone(docbank.write_number("+17565550142", f)) for f in docbank.FORMATS)
    assert not any(parse_phone(line) for line in docbank.FILLER + docbank.REQUEST_FILLER)   # filler holds no phone


def test_documents_ingest():
    # Every generated document is the page model ingest() takes, and ingests without error with a named reader.
    for plan in _docs():
        result = ingest_all([docgen.render(plan)], extractor_version=docgen.EXTRACTOR, reader=StubReader())
        assert [d["id"] for d in result.documents] == [plan.id]
    for world in _worlds():
        rendered = [docgen.render(p) for p in world.documents]
        result = ingest_all(rendered, extractor_version=docgen.EXTRACTOR, reader=StubReader())
        assert [d["id"] for d in result.documents] == [p.id for p in world.documents]
    for case in sorted(FROZEN.glob("*/case.json")):          # the frozen cases' documents too
        _, documents, _ = docgen.load_case(case.parent)
        ingest_all(documents, extractor_version=docgen.EXTRACTOR, reader=StubReader())
    # The plan is the truth: each number's claim is what its phrasing states, with the author's party
    # when the phrasing names none (a quoted number takes the quoted author's).
    for plan in [*_docs(), *(p for w in _worlds() for p in w.documents)]:
        for author, n in docgen.numbers_with_authors(plan):
            assert (n.role, n.party) == docbank.claim(n.phrasing, author), (plan.id, n)


def test_plan_round_trips(tmp_path):
    plans = [*_docs()[:200], *_worlds()]
    for k, plan in enumerate(plans):
        assert docgen.plan_from_json(json.loads(json.dumps(docgen.plan_to_json(plan)))) == plan
        case = docgen.freeze_case(plan, f"case-{k}", tmp_path, patterns=["G3/main/label_number"], why="a round trip")
        back, documents, record = docgen.load_case(case)
        assert back == plan
        assert documents == docgen.rendered(plan)
        assert record["patterns"] == ["G3/main/label_number"]
    # A frozen case is never replaced by a different plan; freezing the same plan again changes nothing.
    first, second = plans[0], plans[1]
    docgen.freeze_case(first, "kept", tmp_path, patterns=["G1/x/y"])
    docgen.freeze_case(first, "kept", tmp_path, patterns=["G2/x/y"])
    assert docgen.load_case(tmp_path / "kept")[2]["patterns"] == ["G1/x/y"]
    with pytest.raises(FileExistsError):
        docgen.freeze_case(second, "kept", tmp_path)


def test_coverage_floor():
    # Over the seed-0 sample, every phrasing, placement, format, visibility, author and owner appears at
    # its stated floor (docgen.FLOORS), and the check can fail: drop one value and it does.
    assert docgen.below_floor(_docs()) == []
    assert set(docgen.FLOORS) >= {"phrasing", "placement", "format", "visibility", "author", "owner"}
    without = [p for p in _docs() if all(n.phrasing != "fax_forms" for _, n in docgen.numbers_with_authors(p))]
    assert any("fax_forms" in problem for problem in docgen.below_floor(without))
    without = [p for p in _docs() if p.author != "unrelated"]
    assert any("unrelated" in problem for problem in docgen.below_floor(without))


def test_draws_are_pinned_by_the_seed():
    # Decision 7 (FINDINGS B10): a seeded random.Random, so a seed gives the same plans everywhere, and a
    # smaller sample is the start of a larger one.
    assert docgen.draw_docs(examples=50, seed=SEED) == list(_docs()[:50])
    assert docgen.draw_docs(examples=50, seed=1) != list(_docs()[:50])


# --- Fictional numbers -------------------------------------------------------------------------------

def test_rendered_text_holds_only_fictional_numbers(tmp_path):
    # Every number in the plans and rendered documents is in NPA-555-0100 to 0199. The scan reads the
    # rendered text with phonenumbers (lenient), not an E.164 pattern, and it can fail.
    real_looking = "+1201" + "5551234"                    # built in pieces so no file scan sees it whole
    assert docgen.real_looking(["Call (415) 555-0142", "Or call 201-555-" + "1234 today"]) == [real_looking]
    plans = [*_docs(), *_worlds()]
    texts = [span["text"] for plan in plans for d in docgen.rendered(plan) for span in d["pages"][0]["spans"]]
    for case in sorted(FROZEN.glob("*/case.json")):
        _, documents, _ = docgen.load_case(case.parent)
        texts += [span["text"] for d in documents for page in d["pages"] for span in page["spans"]]
    assert docgen.real_looking(texts) == []
    assert all(docgen.fictional(n) for plan in plans for n in docgen.all_numbers(plan))
    # Nothing real-looking is ever frozen, whether in the plan or only in its rendered text.
    plan = _docs()[0]
    bad = dataclasses.replace(plan, numbers=(dataclasses.replace(plan.numbers[0], e164=real_looking), *plan.numbers[1:]))
    with pytest.raises(ValueError, match="real-looking"):
        docgen.freeze_case(bad, "bad", tmp_path)
    worded = dataclasses.replace(plan, filler=("Ring 201-555-" + "1234 after hours",))
    with pytest.raises(ValueError, match="real-looking"):
        docgen.freeze_case(worded, "worded", tmp_path)


# --- The checks, G1 to G7 ----------------------------------------------------------------------------

_ROLE_WORDS = {"fax": "fax", "billing": "billing", "receivable": "ar", "main": "main", "office": "main"}


class TruthReader:
    """Labels each number with exactly what its plan claims: any reader this good passes every check, so
    a failure below is the stand-in's, not the check's."""

    def __init__(self, plan: docgen.DocPlan):
        self.truth = {n.e164: n for _, n in docgen.numbers_with_authors(plan)}

    def label(self, visible_text: str) -> list[dict]:
        out = []
        for line in visible_text.splitlines():
            found = parse_phone(line)
            for e164 in found:
                n = self.truth.get(e164)
                if n is None:
                    continue
                quote = line if len(found) == 1 else next(s for s in line.split("   ") if parse_phone(s) == [e164])
                out.append({"quote": quote, "role": n.role or "unknown", "party": n.party or "unknown"})
        return out


class AllVendor:
    """G3's stand-in: every number is the vendor's main line."""

    def label(self, visible_text: str) -> list[dict]:
        return [{"quote": ln, "role": "main", "party": "vendor"} for ln in visible_text.splitlines()
                if len(parse_phone(ln)) == 1]


class SwapBankVendor:
    """G4's stand-in: the stub, with bank and vendor swapped."""

    def label(self, visible_text: str) -> list[dict]:
        swap = {"bank": "vendor", "vendor": "bank"}
        return [{**x, "party": swap.get(x["party"], x["party"])} for x in StubReader().label(visible_text)]


class PartyByFormat:
    """G5's stand-in: the party depends on how the number is written."""

    def label(self, visible_text: str) -> list[dict]:
        import re
        return [{"quote": ln, "role": "main", "party": "vendor" if re.search(r"\(\d{3}\)", ln) else "bank"}
                for ln in visible_text.splitlines() if len(parse_phone(ln)) == 1]


class NearestLabel:
    """G6's stand-in: a number takes the nearest label anywhere in the document."""

    def label(self, visible_text: str) -> list[dict]:
        import re
        lines, labeled = visible_text.splitlines(), {}
        for i, ln in enumerate(lines):
            words = re.findall(r"[a-z]+", ln.split(":", 1)[0].lower()) if ":" in ln else []
            roles = [_ROLE_WORDS[w] for w in words if w in _ROLE_WORDS]
            if roles:
                labeled[i] = (roles[0], "bank" if "bank" in ln.lower() else "vendor")
        out = []
        for i, ln in enumerate(lines):
            if len(parse_phone(ln)) == 1 and labeled:
                j = min(labeled, key=lambda k: (abs(k - i), k))
                out.append({"quote": ln, "role": labeled[j][0], "party": labeled[j][1]})
        return out


def _leaky_ingest_all(documents, *, extractor_version, reader):
    """G2's stand-in: an ingestion that hands hidden lines to the reader, and lets grounding match them."""
    from dialback_ledger.candidates import find_phones
    from dialback_ledger.grounding import ground
    from dialback_ledger.ingest import IngestResult
    from dialback_ledger.text import lines, visible_text
    result = IngestResult()
    for d in documents:
        ls = lines(d)
        shown = [dataclasses.replace(ln, visibility="visible") for ln in ls]
        cands = find_phones(ls)
        accepted, _ = ground(reader.label(visible_text(shown)), shown,
                             [dataclasses.replace(c, visibility="visible") for c in cands])
        by_place = {(g.candidate.line, g.candidate.start): g for g in accepted}
        result.documents.append({"id": d["id"]})
        for c in cands:
            g = by_place.get((c.line, c.start))
            result.observations.append({"document_id": d["id"], "value_normalized": c.e164, "visibility": c.visibility,
                                        "role": g.role if g else "unknown", "party": g.party if g else "unknown"})
    return result


def _drop_plus_format(document_lines):
    """G1's stand-in: candidates.py, except that it drops numbers written `+1 415 555 0142`."""
    from dialback_ledger.candidates import find_phones
    return [c for c in find_phones(document_lines) if not c.raw.startswith("+")]


def test_each_check_catches_its_stand_in(monkeypatch):
    plans = _docs()[:300]
    for plan in plans:          # a reader that reads the plan's truth passes every check
        assert docgen.check_doc(plan, reader=TruthReader(plan)).violations == [], plan.id
    caught = {
        "G2": docgen.check_docs(plans, reader=StubReader(), ingest=_leaky_ingest_all, checks=("G2",)),
        "G3": docgen.check_docs(plans, reader=AllVendor(), checks=("G3",)),
        "G4": docgen.check_docs(plans, reader=SwapBankVendor(), checks=("G4",)),
        "G5": docgen.check_docs(plans, reader=PartyByFormat(), checks=("G5",)),
        "G6": docgen.check_docs(plans, reader=NearestLabel(), checks=("G6",)),
    }
    monkeypatch.setattr("dialback_ledger.ingest.find_phones", _drop_plus_format)
    caught["G1"] = docgen.check_docs(plans, reader=StubReader(), checks=("G1",))
    for check, result in caught.items():
        assert result.violations and {v.check for v in result.violations} == {check}, check


@pytest.fixture
def _today_rule(monkeypatch):
    """Today's rule, unguarded: spec 0079's guard (DIALBACK_CONTEXT_GUARD, on once it lands) refuses to move
    the call. Before spec 0079 the flag is ignored (as in test_invariants.py)."""
    monkeypatch.setenv("DIALBACK_CONTEXT_GUARD", "off")


class _ContextBlind:
    """Spec 0083's context-blind fix shape, as a rule module: a sighting in select()'s own change context
    keeps its timing and visibility but loses its party and role."""
    from dialback_ledger.select import change_context_documents

    @staticmethod
    def select(**kw):
        from dialback_ledger.select import change_context_documents, select
        ctx = {d.id for d in change_context_documents(**kw)}
        rows = [dataclasses.replace(s, party="vendor", role="main") if s.document_id in ctx else s for s in kw["sightings"]]
        return select(**{**kw, "sightings": rows})


class _EverythingEligible:
    """A broken rule that calls every number eligible, so G7's I1 half has something to catch."""
    from dialback_ledger.select import change_context_documents

    @staticmethod
    def select(**kw):
        from dialback_ledger.select import select
        out = select(**kw)
        return {**out, "candidates": [{**c, "eligible": True, "reasons": []} for c in out["candidates"]]}


def _a1_world_plan(mention: str = "bank_verification_line") -> docgen.WorldPlan:
    """A generated world in A1's shape: a planted provable number, and the change request calling the
    vendor's real number something other than the vendor's."""
    plan = next(w for w in _worlds() if w.planted)
    req = next(d for d in plan.documents if d.id == plan.request)
    real = docgen.NumberPlan(plan.real, "vendor", *docbank.claim(mention, "attacker"), mention, "label_number",
                             "paren", "visible")
    docs = tuple(dataclasses.replace(d, numbers=(real,)) if d.id == req.id else d   # follow-ups dropped
                 for d in plan.documents if not d.id.split("-")[-1].startswith("f"))
    return dataclasses.replace(plan, documents=docs)


def test_g7_fails_today_and_passes_context_blind(_today_rule):
    from dialback_evals.gencorpus import load_world
    a1 = load_world(ROOT / "evals" / "corpus-generated" / "planted-number-picked-after-in-thread-bank-label")
    assert docgen.g7(a1) == [docgen.G7_MOVED]                   # today's rule moves the call (A1)
    assert docgen.g7(a1, rule=_ContextBlind) == []              # the context-blind rule does not
    generated = docgen.world_of(_a1_world_plan(), reader=StubReader())
    assert docgen.g7(generated) == [docgen.G7_MOVED]
    assert docgen.g7(generated, rule=_ContextBlind) == []
    found = docgen.check_world(_a1_world_plan(), reader=StubReader())
    assert [v.pattern for v in found] == ["G7/bank_verification_line/label_number"]
    # The I1 half can fail too: a number first seen in the change context, called eligible by a broken rule.
    plan = _a1_world_plan()
    fresh = docgen.NumberPlan("+12015550111", "attacker", "ar", "vendor", "accounts_receivable", "label_number",
                              "paren", "visible")
    with_fresh = dataclasses.replace(plan, documents=tuple(
        dataclasses.replace(d, numbers=(fresh,)) if d.id == plan.request else d for d in plan.documents))
    assert docgen.G7_FIRST in docgen.g7(docgen.world_of(with_fresh, reader=StubReader()), rule=_EverythingEligible)
    # Every generated world is sound: without the change context's numbers, the real number is eligible on the stub.
    for world in _worlds():
        quiet = docgen.world_of(docgen.without_context_numbers(world), reader=StubReader())
        out = docgen.run_rule(quiet)
        assert any(c["e164"] == world.real and c["eligible"] for c in out["candidates"]), world.id


# --- Known failures by pattern (decision 6) ----------------------------------------------------------

SMALL = 40   # a small run for the known-failures tests; the gate runs 1000


def _lab_docs(tmp_path, entries: list[dict], capsys) -> tuple[int, str]:
    from dialback_evals.cli import main
    listed = tmp_path / "known.json"
    listed.write_text(json.dumps({"lab-docs": entries}))
    code = main(["docs", "--examples", str(SMALL), "--seed", str(SEED), "--out", str(tmp_path / "frozen"),
                 "--known-failures", str(listed)])
    return code, capsys.readouterr().out


def _failing_now() -> list[str]:
    run = docgen.run(examples=SMALL, seed=SEED, reader=StubReader())
    return sorted({v.pattern for v in run.violations})


def _entry(pattern: str, finding: str = "A19") -> dict:
    check, phrasing, placement = pattern.split("/")
    return {"check": check, "phrasing": phrasing, "placement": placement, "finding": finding, "owner": "Inesh"}


def test_unlisted_pattern_turns_red(tmp_path, capsys):
    failing = _failing_now()
    assert len(failing) >= 2                              # the stub fails some patterns even in a small run
    code, out = _lab_docs(tmp_path, [_entry(p) for p in failing], capsys)
    assert code == 0, out                                 # every failure listed, every listed one failing
    left_out = failing[0]
    code, out = _lab_docs(tmp_path, [_entry(p) for p in failing[1:]], capsys)
    assert code == 1 and f"{left_out} fails with {left_out.split('/')[0]}, which the known-failures list" in out, out


def test_listed_pattern_that_passes_turns_red(tmp_path, capsys):
    failing = _failing_now()
    passing = "G3/main/table_row"                         # the stub never labels a table row's number
    assert passing not in failing
    code, out = _lab_docs(tmp_path, [_entry(p) for p in [*failing, passing]], capsys)
    assert code == 1 and f"{passing} is on the known-failures list" in out and "no longer fails" in out, out


def test_a_pattern_entry_needs_every_field(tmp_path):
    from dialback_evals import known
    good = _entry("G3/main/label_number")
    path = tmp_path / "known.json"
    path.write_text(json.dumps({"lab-docs": [good]}))
    assert known.load(path)["lab-docs"] == (known.Entry("G3/main/label_number", ("G3",), "A19", "Inesh"),)
    path.write_text(json.dumps({"lab-docs": [{k: v for k, v in good.items() if k != "placement"}]}))
    with pytest.raises(ValueError, match="placement"):
        known.load(path)
