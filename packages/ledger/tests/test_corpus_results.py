"""Spec 0038: the dashboard's corpus page reads a file the real rule produced, and it is current."""
import importlib.util

from _world import ROOT

spec = importlib.util.spec_from_file_location("corpus_results", ROOT / "scripts" / "corpus" / "results.py")
results = importlib.util.module_from_spec(spec)
spec.loader.exec_module(results)


def test_the_committed_results_are_what_the_rule_does_today():
    assert results.OUT.read_text() == results.render(results.build()), "run: uv run python scripts/corpus/results.py"


def test_every_case_matches_its_frozen_truth_and_none_is_left_out():
    doc = results.build()
    assert len(doc["cases"]) == 20 and all(c["matches"] for c in doc["cases"])
    assert {c["kind"] for c in doc["cases"]} == {"poison", "control", "known_limit"}
