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
