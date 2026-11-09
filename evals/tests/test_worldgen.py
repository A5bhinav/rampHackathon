"""Spec 0033, Tasks 1 and 1b: the generator emits valid select() inputs, make_world is deterministic,
the generator actually reaches every rejection reason and knob path, and clean worlds stay clean."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from dialback_evals.worldgen import NumberSpec, clean_worlds, make_world, profile, run, worlds
from hypothesis import given, settings
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "packages/contracts/schemas/selection.schema.json").read_text())
REASONS = {"first_seen_in_change_context", "too_few_source_types", "younger_than_minimum_age",
           "near_miss_of_older_number", "hidden_text_only", "not_vendor_party", "fax_line"}
KNOBS = {"no_request", "late", "shared_id", "reply_subject", "subdomain", "lookalike", "near_miss_pair",
         "mixed_visibility", "mixed_party", "mixed_timing", "non_default_settings"}


def test_generated_world_runs_and_validates():
    @given(worlds())
    @settings(max_examples=200, deadline=None)
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
    sample()
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
