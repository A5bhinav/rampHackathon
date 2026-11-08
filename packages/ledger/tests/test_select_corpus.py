"""Spec 0021, the gate: ledger-select-v1 does what the frozen corpus truth says (spec 0020)."""
from pathlib import Path

import pytest
from _world import ROOT, SCHEMA, at, corpus_world, run
from jsonschema import Draft202012Validator

TRUTHS = sorted((ROOT / "corpus" / "truth").glob("*.json"))


def test_the_corpus_is_there():
    assert len(TRUTHS) == 20  # P01-P12 (P04 in three variants), K01, C01-C05


@pytest.mark.parametrize("path", TRUTHS, ids=lambda p: p.stem)
def test_matches_the_frozen_truth(path: Path):
    truth, inputs = corpus_world(path)
    out = run(inputs)
    exp = truth["expected"]
    assert list(Draft202012Validator(SCHEMA).iter_errors(out)) == []
    assert at(out["cutoff"]) == at(exp["cutoff"])
    assert out["outcome"] == exp["outcome"]
    assert out.get("e164") == exp["pick"]
    by_number = {c["e164"]: c for c in out["candidates"]}
    for e164, reasons in exp["rejected"].items():
        assert not by_number[e164]["eligible"], e164
        assert set(reasons) <= set(by_number[e164]["reasons"]), (e164, by_number[e164]["reasons"])
    for e164 in exp.get("eligible", []):
        assert by_number[e164]["eligible"], (e164, by_number[e164]["reasons"])
    for e164, when in exp.get("first_seen_at", {}).items():
        assert at(by_number[e164]["first_seen_at"]) == at(when)
    poison = {p["e164"] for p in truth["planted"] if p["truth"] == "poison"}
    if truth["kind"] != "known_limit":
        assert out.get("e164") not in poison, "a poisoned number was picked"
