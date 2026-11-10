"""Spec 0083, Tasks 1 and 1b: the generator emits valid select() inputs, make_world is deterministic,
the generator actually reaches every rejection reason and knob path, and clean worlds stay clean."""
from __future__ import annotations

import json
import multiprocessing
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dialback_evals import coverage
from dialback_evals.draws import world_digests
from dialback_evals.worldgen import (
    REAL_LINE,
    NumberSpec,
    SightingSpec,
    clean_worlds,
    make_world,
    profile,
    run,
    worlds,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
FICTIONAL = re.compile(r"^\+1\d{3}55501\d{2}$")   # NANP 555-0100 to 555-0199, reserved for fiction
SCHEMA = json.loads((ROOT / "packages/contracts/schemas/selection.schema.json").read_text())
OTHER = "+13125550142"   # a second provable number, fictional


def test_generated_world_runs_and_validates():
    @given(worlds())
    @settings(max_examples=200, deadline=None, derandomize=True, database=None)   # a fixed seed (B7, M6)
    def check(world):
        out = run(world)
        assert list(Draft202012Validator(SCHEMA).iter_errors(out)) == []
    check()


def test_make_world_is_deterministic():
    spec = NumberSpec(e164="+14155550142", channels=["contract", "listing"], lead_days=400,
                      visibility="visible", role="ar", party="vendor", sender="own",
                      in_thread=False, subject_in_context=False, near_miss_of=None, is_poison=False)
    a, b = make_world(numbers=[spec]), make_world(numbers=[spec])
    assert run(a)["inputs_sha256"] == run(b)["inputs_sha256"]


def test_generated_numbers_are_fictional():
    # Review finding C1: every number a generated world holds (and so anything lab fuzz can freeze)
    # must be in the block reserved for fiction, never a real-looking number.
    @given(st.one_of(worlds(), clean_worlds()))
    @settings(max_examples=400, deadline=None, derandomize=True, database=None)
    def check(world):
        numbers = {s.e164 for s in world.kwargs["sightings"]}
        assert numbers and all(FICTIONAL.match(n) for n in numbers), sorted(n for n in numbers if not FICTIONAL.match(n))
    check()


def _coverage_sample(strategy) -> list:
    """The fixed, seeded sample the coverage floor is read on: 600 worlds, derandomized, pinned draws."""
    drawn = []

    @given(strategy)
    @settings(max_examples=600, deadline=None, derandomize=True, database=None)
    def sample(world):
        drawn.append(world)
    sample()
    return drawn


def test_generator_covers_every_reason_and_knob():
    # Every reason fires in >= 5% of worlds, every knob path in >= 3%; I12 needs a second provable number
    # for the call to move to (>= 20%), and its removal half a context label on the pick (>= 5%); and the
    # safety invariants are trivially true in a world with nothing eligible, so at least half the worlds
    # must hold an eligible number (floors set Nov 8 before measuring; the old mix gave 3.7% and 1.2% on
    # the two-provable floors). The share of worlds with an eligible number other than the real line is
    # reported next to that floor with no bar (spec 0048 C3; B7 M9, decided Nov 10).
    seen, n = coverage.measure(_coverage_sample(worlds()), REAL_LINE)
    assert coverage.shortfalls(seen, n) == {}, coverage.summary(seen, n)
    assert set(coverage.summary(seen, n)) >= {"some_eligible", "eligible_other_than_real"}


# Spec 0048, C3 (FINDINGS B6 and B7 M10).

def test_context_label_counts_only_on_the_pick():
    # Two provable numbers; the attacker's in-thread bank line names one of them. Only a label on the
    # number the call would go to (the reference's pick: the older one here) counts for I12's floor.
    label = SightingSpec("email", 10, party="bank", in_thread=True, sender="other")
    on_other = make_world(numbers=[NumberSpec(REAL_LINE, ["contract", "listing"], 500),
                                   NumberSpec(OTHER, [], 0, sightings=(SightingSpec("contract", 400),
                                                                      SightingSpec("listing", 400), label))])
    on_pick = make_world(numbers=[NumberSpec(OTHER, ["contract", "listing"], 400),
                                  NumberSpec(REAL_LINE, [], 0, sightings=(SightingSpec("contract", 500),
                                                                          SightingSpec("listing", 500), label))])
    for world in (on_other, on_pick):
        assert coverage.reference_provable(world)[0] == {REAL_LINE, OTHER}
    assert coverage.measure([on_other], REAL_LINE)[0]["context_label_on_provable"] == 0
    assert coverage.measure([on_pick], REAL_LINE)[0]["context_label_on_provable"] == 1


def test_the_eligible_floor_counts_any_eligible_number_and_reports_the_rest():
    # Spec 0048 C3 (Sami, Nov 10): the floor keeps its original count (any eligible number, the real line
    # included, against 50%), and the share holding an eligible number other than the real line is reported
    # next to it with no bar (B7, M9).
    only_real = make_world(numbers=[NumberSpec(REAL_LINE, ["contract", "listing"], 500)])
    with_other = make_world(numbers=[NumberSpec(REAL_LINE, ["contract", "listing"], 500),
                                     NumberSpec(OTHER, ["contract", "listing"], 400)])
    nothing = make_world(numbers=[NumberSpec(REAL_LINE, ["contract", "listing"], 10)])        # too young
    assert run(only_real)["e164"] == REAL_LINE and run(nothing).get("e164") is None
    seen, n = coverage.measure([only_real, with_other], REAL_LINE)
    assert (seen["some_eligible"], seen["eligible_other_than_real"]) == (2, 1)
    report = coverage.summary(seen, n)
    assert report["some_eligible"] == 1.0 and report["eligible_other_than_real"] == 0.5   # both in the output
    # Pass or fail depends only on the original count: no eligible number other than the real line at all
    # still passes; nothing eligible at all fails.
    only, m = coverage.measure([only_real, only_real], REAL_LINE)
    assert coverage.summary(only, m)["eligible_other_than_real"] == 0.0
    assert "some_eligible" not in coverage.shortfalls(only, m)
    assert "eligible_other_than_real" not in coverage.shortfalls(only, m)
    none, k = coverage.measure([nothing, nothing], REAL_LINE)
    assert coverage.shortfalls(none, k)["some_eligible"] == 0.0


def _first_cut():
    """The first-cut generator (one shared profile per number), from history: tests/fixtures."""
    import importlib.util
    source = (ROOT / "evals" / "tests" / "fixtures" / "worldgen-first-cut.py.txt").read_text(encoding="utf-8")
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("lab_worldgen_first_cut", loader=None))
    sys.modules[module.__name__] = module                  # its dataclasses look their module up there
    exec(compile(source, "worldgen-first-cut.py", "exec"), module.__dict__)   # noqa: S102
    return module


def test_first_cut_generator_fails_coverage():
    # Spec 0083's acceptance: "the first-cut generator (one shared profile per number) fails this check".
    first = _first_cut()
    seen, n = coverage.measure(_coverage_sample(first.worlds()), first._e164(2025550100))
    short = coverage.shortfalls(seen, n)
    # One profile per number: no number is ever seen both visible and hidden, by two parties or at two times.
    assert {"mixed_visibility", "mixed_party", "mixed_timing"} <= set(short), short


def test_clean_worlds_have_no_attacker_knob():
    @given(clean_worlds())
    @settings(max_examples=300, deadline=None, derandomize=True, database=None)
    def check(world):
        p = profile(world)
        assert not any(p[k] for k in ("no_request", "late", "shared_id", "reply_subject", "lookalike",
                                      "near_miss_pair", "mixed_visibility", "mixed_party"))
        assert all(s.visibility == "visible" and s.party == "vendor" and s.role != "fax"
                   for s in world.kwargs["sightings"])
    check()


# Spec 0048, C1 (FINDINGS B10): Hypothesis 6.170 also draws literals it mined from every first-party
# module loaded at that moment, so one seed drew different worlds in a bare script, in the CLI's
# process, in a bench worker and under pytest. Under pinned draws a seed gives the same worlds in all.
PIN_EXAMPLES = 60
_DIGESTS = """
import json
{imports}
from dialback_evals.draws import world_digests
print(json.dumps(world_digests(examples={examples}, seed=0)))
"""


def _digests_in_a_subprocess(imports: str) -> list[str]:
    code = _DIGESTS.format(imports=imports, examples=PIN_EXAMPLES)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT)
    return json.loads(done.stdout)


def test_a_seed_pins_the_worlds():
    import dialback_evals.cli  # noqa: F401  everything the CLI loads is loaded here too
    here = world_digests(examples=PIN_EXAMPLES, seed=0)                 # under pytest, in process
    bare = _digests_in_a_subprocess("")                                # only the generator and its oracle
    cli = _digests_in_a_subprocess("import dialback_evals.cli")       # what `lab` loads
    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn")) as pool:
        worker = pool.submit(world_digests, examples=PIN_EXAMPLES, seed=0).result()   # a bench worker
    assert len(set(here)) > PIN_EXAMPLES // 2          # a real sample, not one world drawn again and again
    assert {"bare script": bare == here, "cli": cli == here, "worker": worker == here} == {
        "bare script": True, "cli": True, "worker": True}


def test_property_tests_draw_pinned():
    # C1: every test here runs inside pinned_draws (evals/tests/conftest.py), so a property test's
    # sample does not depend on what the test session happened to import.
    from dialback_evals import draws
    assert draws._providers._get_local_constants is draws._Constants


def _given_tests() -> dict[str, bool]:
    """Every @given test function under evals/tests, and whether it has a fixed seed (derandomize or @seed)."""
    import ast
    found = {}
    for path in sorted((ROOT / "evals" / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef):
                continue
            calls = [d for d in node.decorator_list if isinstance(d, ast.Call)]
            names = {getattr(d.func, "id", getattr(d.func, "attr", "")) for d in calls}
            if "given" not in names:
                continue
            seeded = "seed" in names or any(k.arg == "derandomize" and getattr(k.value, "value", False) is True
                                            for d in calls for k in d.keywords)
            found[f"{path.name}:{node.lineno}:{node.name}"] = seeded
    return found


def test_every_property_test_has_a_fixed_seed():
    # FINDINGS B7, M6: a property test without a fixed seed draws a new sample on every run.
    found = _given_tests()
    assert len(found) >= 8                            # the scan really sees the property tests
    assert sorted(name for name, seeded in found.items() if not seeded) == []
