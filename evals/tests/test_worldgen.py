"""Spec 0030, Task 1: the generator emits valid select() inputs, and make_world is deterministic."""
from __future__ import annotations

import json
from pathlib import Path

from dialback_evals.worldgen import NumberSpec, make_world, run, worlds
from hypothesis import given, settings
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "packages/contracts/schemas/selection.schema.json").read_text())


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
