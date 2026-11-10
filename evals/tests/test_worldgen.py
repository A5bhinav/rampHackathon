"""Spec 0033, Tasks 1 and 1b: the generator emits valid select() inputs, make_world is deterministic,
the generator actually reaches every rejection reason and knob path, and clean worlds stay clean."""
from __future__ import annotations

import datetime as dt
import json
import multiprocessing
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dialback_evals.draws import world_digests
from dialback_evals.invariants import _fax, _frame, _near_flagged
from dialback_evals.worldgen import NumberSpec, clean_worlds, make_world, profile, run, worlds
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
FICTIONAL = re.compile(r"^\+1\d{3}55501\d{2}$")   # NANP 555-0100 to 555-0199, reserved for fiction
SCHEMA = json.loads((ROOT / "packages/contracts/schemas/selection.schema.json").read_text())
REASONS = {"first_seen_in_change_context", "too_few_source_types", "younger_than_minimum_age",
           "near_miss_of_older_number", "hidden_text_only", "not_vendor_party", "fax_line"}
KNOBS = {"no_request", "late", "shared_id", "reply_subject", "subdomain", "lookalike", "near_miss_pair",
         "mixed_visibility", "mixed_party", "mixed_timing", "non_default_settings", "twin_first",
         "request_label", "follow_up", "early_cutoff",
         # spec 0048, C2: the boundaries B5 found unreached
         "no_thread", "bare_request", "late_sighting", "upper_fax", "same_time_near_miss", "age_at_min_age",
         "age_at_established", "age_at_subject_window", "context_role_claim"}


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


def _reference_provable(world) -> tuple[set[str], set[str]]:
    """The numbers the oracle's reference frame finds nothing against (I1-I5, I7), read from raw inputs
    rather than from the rule, so a fixed rule cannot move these floors; and those of them that also
    carry a visible label in the change context (what I12's removal half needs)."""
    f, s = _frame(world), world.settings
    ok, labeled = set(), set()
    for e164, n in f.numbers.items():
        if n.first_seen_in_context or not n.visible or len(n.channels) < s.min_source_types:
            continue
        if n.first_clean is None or f.cutoff - n.first_clean < dt.timedelta(days=s.min_age_days):
            continue
        if any((r.party != "vendor" or _fax(r)) and r.document_id not in f.in_context_ids for r in n.visible):
            continue
        if _near_flagged(f, e164, s):
            continue
        ok.add(e164)
        if any((r.party != "vendor" or _fax(r)) and r.document_id in f.in_context_ids for r in n.visible):
            labeled.add(e164)
    return ok, labeled


def test_generator_covers_every_reason_and_knob():
    seen, n = Counter(), [0]

    @given(worlds())
    @settings(max_examples=600, deadline=None, derandomize=True, database=None)
    def sample(world):
        n[0] += 1
        out = run(world)
        seen.update({r for c in out["candidates"] for r in c["reasons"]})
        seen.update(k for k, v in profile(world).items() if v)
        seen["some_eligible"] += any(c["eligible"] for c in out["candidates"])
        ok, labeled = _reference_provable(world)
        seen["two_provable"] += len(ok) >= 2
        seen["context_label_on_provable"] += len(ok) >= 2 and bool(labeled)
    sample()
    # I12 needs a second provable number for the call to move to, and its removal half needs a context
    # label on a provable number (floors set Nov 8 before measuring; the old mix gave 3.7% and 1.2% on this sample).
    floors = {"two_provable": 0.20, "context_label_on_provable": 0.05}
    short = {k: round(seen[k] / n[0], 3) for k, bar in floors.items() if seen[k] / n[0] < bar}
    assert short == {}, short
    low = {k: round(seen[k] / n[0], 3) for k in sorted(REASONS | KNOBS)
           if seen[k] / n[0] < (0.05 if k in REASONS else 0.03)}
    assert low == {}, low   # every reason fires in >= 5% of worlds, every knob path in >= 3%
    # The safety invariants are trivially true in a world with nothing eligible, so at least half the
    # worlds must hold an eligible number for them to scrutinize.
    assert seen["some_eligible"] / n[0] >= 0.5, round(seen["some_eligible"] / n[0], 3)


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
