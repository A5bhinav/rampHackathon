"""Spec 0033, Task 2: the invariant oracle holds on the real rule, and every label fires when a
deliberately broken rule lets its case through (so each check can actually fail)."""
from __future__ import annotations

import itertools

import pytest
from dialback_evals.invariants import all_violations, l1_premise, liveness, metamorphic, violations
from dialback_evals.worldgen import NumberSpec, SightingSpec, clean_worlds, make_world, run, worlds
from hypothesis import given, settings

REAL = "+14155550142"
NUM = "+13035550169"
X = "+13035550101"


def _rewrite(out: dict, e164: str, **changes) -> dict:
    """A doctored select() output: the named candidate rewritten, everything else as the rule said."""
    return {**out, "candidates": [{**c, **changes} if c["e164"] == e164 else dict(c) for c in out["candidates"]]}


def _eligible_if(out: dict, e164: str, flag: bool) -> dict:
    return {**out, "candidates": [{**c, "eligible": c["eligible"] or (c["e164"] == e164 and flag)}
                                  for c in out["candidates"]]}


def test_all_invariants_hold_on_the_real_rule():
    @given(worlds())
    @settings(max_examples=2000, deadline=None, derandomize=True, database=None)
    def check(world):
        assert all_violations(world) == []
    check()


@pytest.mark.parametrize(("label", "spec"), [
    ("I1_incontext_only_eligible", NumberSpec(NUM, ["contract", "listing"], 400, in_thread=True)),
    ("I2_hidden_only_eligible", NumberSpec(NUM, ["contract", "listing"], 400, visibility="white")),
    ("I3_nonvendor_or_fax_eligible", NumberSpec(NUM, ["contract", "listing"], 400, party="bank")),
    ("I3_nonvendor_or_fax_eligible", NumberSpec(NUM, ["contract", "listing"], 400, role="fax")),
    ("I4_too_few_channels_eligible", NumberSpec(NUM, ["contract"], 400)),
    ("I5_too_young_eligible", NumberSpec(NUM, ["contract", "listing"], 10)),
])
def test_each_invariant_catches_a_broken_rule(label, spec):
    world = make_world(numbers=[spec])
    honest = run(world)
    assert label not in violations(world, honest)                                # the real rule passes
    assert label in violations(world, _rewrite(honest, NUM, eligible=True, reasons=[]))   # a leaky rule fails


def test_pick_with_reasons_or_bad_schema_trips_i6():
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500)])
    honest = run(world)
    assert violations(world, honest) == []
    assert "I6_pick_has_reasons" in violations(world, _rewrite(honest, REAL, reasons=["fax_line"]))
    assert "I6_schema" in violations(world, {**honest, "outcome": "maybe"})


def test_poison_near_miss_pick_trips_i7():
    real = NumberSpec(REAL, ["contract", "listing"], 200)
    sib = NumberSpec("+14155550143", ["contract", "listing"], 150, near_miss_of=REAL, is_poison=True)
    world = make_world(numbers=[real, sib])
    honest = run(world)
    assert honest["e164"] == REAL and violations(world, honest) == []
    broken = _rewrite(honest, "+14155550143", eligible=True, reasons=[]) | {"e164": "+14155550143"}
    found = set(violations(world, broken))
    assert "I7_poison_pick_below_bar" in found
    assert not found & {"I1_incontext_only_eligible", "I2_hidden_only_eligible", "I4_too_few_channels_eligible",
                        "I5_too_young_eligible"}   # only the near-miss rule stood in its way


def test_established_near_miss_is_not_a_violation():
    # Two vendor numbers one digit apart, both over 365 days old. Roles make the poison sibling the
    # pick (AR outranks main), so the check really runs: the established carve-out must hold.
    base = NumberSpec(REAL, ["contract", "listing"], 800, role="main")
    sib = NumberSpec("+14155550143", ["contract", "listing"], 800, role="ar", near_miss_of=REAL, is_poison=True)
    world = make_world(numbers=[base, sib])
    assert run(world)["e164"] == "+14155550143"
    assert "I7_poison_pick_below_bar" not in all_violations(world)


def test_no_pick_world_is_valid():
    world = make_world(numbers=[NumberSpec("+15035550169", ["email"], 5, in_thread=True, is_poison=True)])
    assert run(world)["outcome"] == "no_provable_number" and all_violations(world) == []


def test_clean_world_picks_a_provable_number():
    held = [0, 0]

    @given(clean_worlds())
    @settings(max_examples=500, deadline=None, derandomize=True, database=None)
    def check(world):
        held[0] += 1
        held[1] += l1_premise(world)
        assert liveness(world, run(world)) == []
    check()
    assert held[1] == held[0]   # clean_worlds always builds a premise-meeting number, so L1 is never vacuous


@pytest.mark.parametrize("label", ["M_determinism", "M_reorder", "M_incontext_helped", "M_hidden_helped",
                                   "M_lookalike_helped"])
def test_each_metamorphic_check_catches_a_broken_rule(label, monkeypatch):
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500), NumberSpec(X, ["contract"], 400)])
    honest = run(world)
    assert metamorphic(world) == []                                              # the real rule passes
    calls = itertools.count()

    def docs(w):
        return w.kwargs["documents"]

    broken = {
        "M_determinism": lambda w: honest | {"inputs_sha256": str(next(calls))},
        "M_reorder": lambda w: honest | {"inputs_sha256": docs(w)[0].id},
        "M_incontext_helped": lambda w: _eligible_if(honest, X, any(d.id == "m_ctx" for d in docs(w))),
        "M_hidden_helped": lambda w: honest | ({"e164": X} if any(s.visibility != "visible"
                                                                  for s in w.kwargs["sightings"]) else {}),
        "M_lookalike_helped": lambda w: _eligible_if(honest, X, any("acrne" in (d.sender or "") for d in docs(w))),
    }[label]
    monkeypatch.setattr("dialback_evals.invariants.run", broken)
    assert label in metamorphic(world)


def test_new_invariants_catch_broken_rules(monkeypatch):
    real = NumberSpec(REAL, ["contract", "listing"], 500)
    picked = run(make_world(numbers=[real]))
    assert "I9_picked_without_request" not in violations(make_world(numbers=[real], request_present=False),
                                                         run(make_world(numbers=[real], request_present=False)))
    assert "I9_picked_without_request" in violations(make_world(numbers=[real], request_present=False), picked)
    empty = {k: v for k, v in picked.items() if k != "e164"} | {"outcome": "no_provable_number"}
    assert liveness(make_world(numbers=[real]), picked) == []
    assert liveness(make_world(numbers=[real]), empty) == ["L1_clean_world_no_pick"]
    late = make_world(numbers=[real, NumberSpec("+14155550199", [], 500,
                                                sightings=(SightingSpec("listing", 500, recorded_late=True),))])
    assert metamorphic(late) == []
    monkeypatch.setattr("dialback_evals.invariants.run",           # a rule that reads late records trips I10
                        lambda w: picked | {"inputs_sha256": str(len(w.kwargs["documents"]))})
    assert "M_late_changed" in metamorphic(late)
    monkeypatch.setattr("dialback_evals.invariants.run",           # a rule that rewards shared ids trips I11
                        lambda w: picked | {"candidates": [{"e164": REAL, "reasons": [],
                                                            "eligible": len({d.id for d in w.kwargs["documents"]})
                                                            < len(w.kwargs["documents"])}]})
    assert "M_shared_id_helped" in metamorphic(make_world(numbers=[real]))
