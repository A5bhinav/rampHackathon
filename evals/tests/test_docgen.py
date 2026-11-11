"""Spec 0052: generated documents with a known claim and owner for every phone number, run through real
ingestion (with a named reader) and select(), held to checks any reader must pass."""
from __future__ import annotations

import dataclasses
import datetime as dt
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
    return tuple(docgen.draw_worlds(seed=SEED))


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



def test_a_frozen_case_says_what_each_violation_was(tmp_path):
    # The fresh review: a G5 case is keyed by its variant's phrasing, which its plan need not contain. A frozen
    # case records each violation's number and detail, so its pattern can be read from the file.
    found = docgen.run(examples=EXAMPLES, seed=SEED, reader=StubReader())
    g5 = next(v for v in found.violations if v.check == "G5")
    shown = {v.pattern for v in found.violations} - {g5.pattern}
    [(name, pattern)] = docgen.freeze_new(found, tmp_path, shown, reader=StubReader())
    assert pattern == g5.pattern
    record = docgen.load_case(tmp_path / name)[2]
    assert sorted({e["pattern"] for e in record["violations"]}) == record["patterns"]
    entry = next(e for e in record["violations"] if e["pattern"] == pattern)
    assert entry["number"] and pattern.split("/")[1] in entry["detail"]

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




def test_g7_blames_only_the_labels_that_move_the_call(_today_rule):
    # Two refusing mentions of the real number in the change context: neither alone moves the call, both
    # together do. An attacker's own number, unlabeled in the context, moves nothing and is never blamed.
    plan = _a1_world_plan()
    def mention(e164, owner, phrasing, placement):
        return docgen.NumberPlan(e164, owner, *docbank.claim(phrasing, "attacker"), phrasing, placement, "paren", "visible")
    follow_up = docgen.DocPlan(f"{plan.id}-f9", "email_body", "attacker", "email", "ar@acme.example", f"{plan.id}-t9",
                               "One more thing", (docgen.T0 + dt.timedelta(minutes=20)).isoformat(),
                               ("Regards,",), (mention("+12015550111", "attacker", "accounts_receivable", "table_row"),
                                               mention(plan.real, "vendor", "main", "number_label")))
    world = dataclasses.replace(plan, documents=(*plan.documents, follow_up))
    found = sorted(v.pattern for v in docgen.check_world(world, reader=StubReader()))
    assert found == ["G7/bank_verification_line/label_number", "G7/main/number_label"]


def test_g7_sweeps_every_layout_of_the_real_number(_today_rule):
    # G7's worlds are a sweep, not a sample: every phrasing and placement the generator renders is the change
    # context's one visible mention of the vendor's real number, once with a planted provable number and once
    # without. So G7's patterns are the same at every seed (the fresh review found 8 of 81 at seed 0 and 14
    # to 16 unlisted ones at seeds 1 to 3 when the worlds were drawn at random). Today's rule, since spec 0091's
    # rule fails none of them, at any seed (the docs gate holds that).
    from collections import Counter
    layouts = [(p, pl) for p in docbank.PHRASINGS for pl in docbank.PLACEMENTS
               if docbank.PHRASINGS[p].form in docbank.PLACEMENT_FORMS[pl]]
    assert len(layouts) == 102
    swept = Counter()
    for world in _worlds():
        context = [d for d in world.documents if not d.id.split("-")[-1].startswith(("rec", "plant"))]
        mentions = [n for d in context for _, n in docgen.numbers_with_authors(d) if n.e164 == world.real]
        assert len(mentions) == 1 and mentions[0].visibility == "visible", world.id
        assert not any(n.e164 != world.real and n.owner != "attacker" for d in context
                       for _, n in docgen.numbers_with_authors(d)), world.id   # no other mention can refuse a call
        swept[(mentions[0].phrasing, mentions[0].placement, world.planted)] += 1
    assert swept == Counter({(p, pl, planted): 1 for p, pl in layouts for planted in (True, False)})
    found = [sorted({v.pattern for w in docgen.draw_worlds(seed=s) for v in docgen.check_world(w, reader=StubReader())})
             for s in (0, 1, 2)]
    assert found[0] and found[0] == found[1] == found[2]

@pytest.mark.docs_gate
def test_checks_on_the_stub():
    # G1 to G7 on the stub at seed 0 with 1000 documents (and every frozen case): every failing pattern is on
    # lab-docs' known-failures list, every listed pattern still fails, and every entry names a written finding.
    from dialback_evals import known
    found = docgen.run(examples=EXAMPLES, seed=SEED, reader=StubReader())
    frozen = docgen.rerun_frozen(FROZEN, reader=StubReader())
    assert [name for name, _, drift in frozen if drift] == []      # each frozen case still means what it meant
    failing = {p: [p.split("/", 1)[0]] for p in {*(v.pattern for v in found.violations),
                                                 *(p for _, patterns, _ in frozen for p in patterns)}}
    entries = known.load(ROOT / "evals" / "known-failures.json")["lab-docs"]
    verdict = known.judge(entries, failing)
    assert verdict.problems == [], "\n".join(verdict.problems)
    assert known.missing_findings(entries, (ROOT / "evals" / "FINDINGS.md").read_text(encoding="utf-8")) == []
    tally = found.result.tally                                     # every check ran on the whole sample
    assert tally.documents == EXAMPLES and tally.worlds == 2 * len(docgen.layouts())
    assert tally.visible_numbers > 1000 and tally.g5_compared > 1000 and tally.g6_compared > 100



@pytest.mark.docs_gate
def test_the_list_is_not_fitted_to_seed_0():
    # At other seeds the sample alone, with no frozen case, fails exactly the listed patterns. The fresh review
    # found 14 to 16 unlisted G7 patterns at seeds 1 to 3 when G7's worlds were sampled; the gate runs seed 0.
    from dialback_evals import known
    listed = {e.case for e in known.load(ROOT / "evals" / "known-failures.json")["lab-docs"]}
    for seed in (1, 2):
        found = {v.pattern for v in docgen.run(examples=EXAMPLES, seed=seed, reader=StubReader()).violations}
        assert (sorted(found - listed), sorted(listed - found)) == ([], []), seed

# --- Known failures by pattern (decision 6) ----------------------------------------------------------

SMALL = 40   # a small run for the known-failures tests; the gate runs 1000


def _frozen_copy(tmp_path) -> Path:
    """A copy of the committed frozen cases, so a run here shrinks only what no committed case shows yet
    (shrinking every G7 pattern into an empty folder takes about 30 seconds a run)."""
    import shutil
    out = tmp_path / "frozen"
    if not out.exists():
        out.mkdir()
        for case in sorted(FROZEN.glob("*/case.json")):
            shutil.copytree(case.parent, out / case.parent.name)
    return out


def _lab_docs(tmp_path, entries: list[dict], capsys) -> tuple[int, str]:
    from dialback_evals.cli import main
    listed = tmp_path / "known.json"
    listed.write_text(json.dumps({"lab-docs": entries}))
    code = main(["docs", "--examples", str(SMALL), "--seed", str(SEED), "--out", str(_frozen_copy(tmp_path)),
                 "--known-failures", str(listed), "--report", str(tmp_path / "report.json")])
    return code, capsys.readouterr().out


def _failing_now(tmp_path) -> list[str]:
    """What a small run fails now: its sample's patterns and its frozen cases' (lab docs holds both)."""
    run = docgen.run(examples=SMALL, seed=SEED, reader=StubReader())
    frozen = docgen.rerun_frozen(_frozen_copy(tmp_path), reader=StubReader())
    return sorted({v.pattern for v in run.violations} | {p for _, patterns, _ in frozen for p in patterns})


def _entry(pattern: str, finding: str = "A19") -> dict:
    check, phrasing, placement = pattern.split("/")
    return {"check": check, "phrasing": phrasing, "placement": placement, "finding": finding, "owner": "Inesh"}


def test_unlisted_pattern_turns_red(tmp_path, capsys):
    failing = _failing_now(tmp_path)
    assert len(failing) >= 2                              # the stub fails some patterns even in a small run
    code, out = _lab_docs(tmp_path, [_entry(p) for p in failing], capsys)
    assert code == 0, out                                 # every failure listed, every listed one failing
    left_out = failing[0]
    code, out = _lab_docs(tmp_path, [_entry(p) for p in failing[1:]], capsys)
    assert code == 1 and f"{left_out} fails with {left_out.split('/')[0]}, which the known-failures list" in out, out


def test_listed_pattern_that_passes_turns_red(tmp_path, capsys):
    failing = _failing_now(tmp_path)
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



def test_another_seed_never_writes_to_the_committed_cases():
    # The fresh review: exploring seed 1 the plain way froze 16 cases into evals/docs-generated, which the
    # seed-0 gate then re-ran and failed on. Without --out, only the gate's seed writes there.
    from dialback_evals import cli
    assert cli._docs_out(None, SEED) == FROZEN
    assert cli._docs_out(None, 1) == ROOT / "evals" / "out" / "docs-generated-seed1"
    assert "evals/out/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert cli._docs_out("somewhere/else", 1) == Path("somewhere/else")


def test_another_seed_is_held_to_the_list_by_default():
    # The second review: `lab docs --seed 5` with no --out was held to no list, so it exited 1 on every listed
    # pattern. With --out left out, auto holds any seed to the repo's list; a folder of your own gets none.
    from dialback_evals import cli
    assert cli._docs_known("auto", None, cli._docs_out(None, 5)) == cli.KNOWN_FAILURES
    assert cli._docs_known("auto", None, cli._docs_out(None, SEED)) == cli.KNOWN_FAILURES
    assert cli._docs_known("auto", "mine", Path("mine")) is None
    assert cli._docs_known("none", None, cli._docs_out(None, 5)) is None
    assert cli._docs_known("listed.json", None, cli._docs_out(None, 5)) == Path("listed.json")

# --- The stub report ---------------------------------------------------------------------------------

def _recount(plans) -> tuple:
    """The over-blocks by author and owner, and the wrong-role counts, recounted from ingest_all directly."""
    from collections import Counter
    by, roles, roles_right, fax, fax_right = Counter(), 0, 0, 0, 0
    for plan in plans:
        if not isinstance(plan, docgen.DocPlan):
            continue
        seen = {o["value_normalized"]: o for o in ingest_all([docgen.render(plan)], extractor_version=docgen.EXTRACTOR,
                                                              reader=StubReader()).observations}
        for author, n in docgen.numbers_with_authors(plan):
            o = seen.get(n.e164)
            if n.visibility != "visible" or o is None:
                continue
            if n.party == "vendor" and n.role is not None and o["party"] == "unknown":
                by[f"{author}/{n.owner}"] += 1
            if n.role is not None and o["role"] not in ("unknown", n.role):
                right = o["party"] == n.party
                roles, roles_right = roles + 1, roles_right + right
                fax, fax_right = fax + (n.role == "fax"), fax_right + (n.role == "fax" and right)
    return dict(sorted(by.items())), roles, roles_right, fax, fax_right

def test_stub_report_has_every_count(tmp_path, capsys, _today_rule):
    from collections import Counter

    from dialback_evals.cli import main
    found = docgen.run(examples=200, seed=SEED, reader=StubReader())
    body = docgen.report(found)
    assert body["unsafe_labels"] == sum(v.check == "G3" for v in found.violations) > 0
    assert body["wrong_parties"] == sum(v.check == "G4" for v in found.violations) > 0
    over = body["overblocks"]
    assert set(over) == {"stub_readable", "not_stub_readable", "fax_lines_among_them", "by_author_and_owner"}
    # Who wrote the over-blocked text, and whose number it is (the fresh review: about half is the attacker's
    # own text, and refusing the attacker's own number is what select() should do; only the vendor's own
    # text is A2's honest over-blocking).
    by = over["by_author_and_owner"]
    assert sum(by.values()) == over["stub_readable"]["left_unlabeled"] + over["not_stub_readable"]["left_unlabeled"]
    assert set(by) <= {"vendor/vendor", "attacker/vendor", "attacker/attacker"} and by["vendor/vendor"] > 0
    assert by["attacker/attacker"] > 0
    # Roles: no check reads them, so the report counts them; a fax line labeled another role is no longer
    # refused as a fax line (A20 does this through "Office hours").
    assert body["wrong_roles"] >= body["fax_lines_labeled_another_role"] > 0
    # The second review: a wrong role with the wrong party is already a G3 failure; one with the party right
    # is seen by no check, and for a fax line that is the case that matters (no longer refused as a fax).
    assert 0 < body["wrong_roles_with_the_party_right"] < body["wrong_roles"]
    assert 0 < body["fax_lines_labeled_another_role_with_the_party_right"] < body["fax_lines_labeled_another_role"]
    # The counts recompute from ingestion directly, outside the code that keeps the tally.
    assert _recount(found.plans.values()) == (over["by_author_and_owner"], body["wrong_roles"],
                                              body["wrong_roles_with_the_party_right"],
                                              body["fax_lines_labeled_another_role"],
                                              body["fax_lines_labeled_another_role_with_the_party_right"])
    # G7 also counts the worlds where change-context text refuses the case and nobody is called (A1's
    # fail-safe side), which I12 allows. Today's rule here: under spec 0091's rule no such world remains.
    assert body["g7_refused_no_call_worlds"] > 0
    # Stub-readable is computed, not hand-tagged, so both sides of the split hold vendor claims; and by its
    # own definition the stub never leaves a stub-readable vendor claim unlabeled.
    assert over["stub_readable"]["vendor_claims_with_a_role"] > 0 and over["not_stub_readable"]["vendor_claims_with_a_role"] > 0
    assert over["stub_readable"]["left_unlabeled"] == 0 < over["not_stub_readable"]["left_unlabeled"]
    g5 = body["g5_stability"]
    assert 0 < g5["same"] < g5["compared"] and g5["rate"] == g5["same"] / g5["compared"]
    assert body["patterns"] == dict(sorted(Counter(v.pattern for v in found.violations).items()))
    assert body["documents"] == 200 and body["worlds"] == 2 * len(docgen.layouts()) and body["g6_compared"] > 0
    # lab docs prints it and writes it.
    path = tmp_path / "report.json"
    main(["docs", "--examples", "200", "--seed", str(SEED), "--out", str(_frozen_copy(tmp_path)), "--known-failures",
          "none", "--report", str(path)])
    out = capsys.readouterr().out
    assert json.loads(path.read_text()) == json.loads(json.dumps(body))
    for words in ("unsafe labels (G3)", "wrong parties (G4)", "stub-readable", "not stub-readable", "G5 stability"):
        assert words in out, words
