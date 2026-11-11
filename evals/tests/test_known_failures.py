"""Spec 0048, D3 (Q101, decided Nov 9): each lab job reads a strict known-failures list,
evals/known-failures.json. A listed entry must still fail with its labels, and one that starts passing
turns the job red so a fix is noticed; anything unlisted that fails turns the job red; a new search hit is
known only when every label it shows belongs to some listed entry, and the job counts such hits; an entry
needs its finding written in FINDINGS."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from dialback_evals import known

ROOT = Path(__file__).resolve().parents[2]
A1 = known.Entry("planted-number-picked", ("M_incontext_label_moved_pick",), "A1", "Inesh")
A5 = known.Entry("near-miss-unanchored", ("M_lookalike_helped", "M_shared_id_helped"), "A5", "Inesh")


def test_a_passing_entry_turns_red():
    v = known.judge([A1, A5], failing={"near-miss-unanchored": ["M_lookalike_helped", "M_shared_id_helped"]})
    assert v.red and any("planted-number-picked" in p and "take it off the list" in p for p in v.problems)
    partly = known.judge([A5], failing={"near-miss-unanchored": ["M_shared_id_helped"]})   # one label stopped
    assert partly.red and any("M_lookalike_helped" in p for p in partly.problems)
    held = known.judge([A1, A5], failing={"planted-number-picked": ["M_incontext_label_moved_pick"],
                                          "near-miss-unanchored": ["M_shared_id_helped", "M_lookalike_helped"]})
    assert not held.red and sorted(held.known) == ["near-miss-unanchored", "planted-number-picked"]


def test_an_unlisted_failure_turns_red():
    failing = {"planted-number-picked": ["M_incontext_label_moved_pick"],
               "near-miss-unanchored": ["M_lookalike_helped", "M_shared_id_helped"]}
    unlisted_case = known.judge([A1, A5], failing={**failing, "a-new-case": ["I5_too_young_eligible"]})
    assert unlisted_case.red and any("a-new-case" in p for p in unlisted_case.problems)
    unlisted_label = known.judge([A1, A5], failing={**failing, "planted-number-picked":
                                                    ["M_incontext_label_moved_pick", "I1_first_seen_in_context_eligible"]})
    assert unlisted_label.red and any("I1_first_seen_in_context_eligible" in p for p in unlisted_label.problems)


def test_a_hit_with_only_listed_labels_is_known_and_counted():
    failing = {"planted-number-picked": ["M_incontext_label_moved_pick"],
               "near-miss-unanchored": ["M_lookalike_helped", "M_shared_id_helped"]}
    v = known.judge([A1, A5], failing=failing, hits=[["M_incontext_label_moved_pick"],
                                                     ["M_shared_id_helped", "M_incontext_label_moved_pick"]])
    assert not v.red and v.known_hits == 2                   # labels from two different entries still count
    new = known.judge([A1, A5], failing=failing, hits=[["M_incontext_label_moved_pick", "I4_too_few_channels_eligible"]])
    assert new.red and new.known_hits == 0 and any("I4_too_few_channels_eligible" in p for p in new.problems)


def test_an_entry_needs_a_finding(tmp_path):
    findings = "# findings\n\n### A1. The change request's own text\n\n### A5. Latent defects\n"
    assert known.missing_findings([A1, A5], findings) == []
    orphan = known.Entry("x", ("L",), "A99", "Abhi")
    assert any("A99" in p for p in known.missing_findings([A1, orphan], findings))
    path = tmp_path / "known.json"
    path.write_text(json.dumps({"lab-fuzz": [{"case": "x", "labels": ["L"], "owner": "Inesh"}]}))
    with pytest.raises(ValueError, match="finding"):
        known.load(path)                                     # an entry without a finding is refused outright


def test_the_shipped_list_holds_its_shape():
    jobs = known.load(ROOT / "evals" / "known-failures.json")
    assert set(jobs) == set(known.JOBS)
    findings = (ROOT / "evals" / "FINDINGS.md").read_text(encoding="utf-8")
    assert known.missing_findings([e for entries in jobs.values() for e in entries], findings) == []
    assert all(e.owner and e.labels for entries in jobs.values() for e in entries)
