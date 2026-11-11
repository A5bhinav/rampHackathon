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


# An entry can hold its labels only for hits on worlds with a shared document id (FINDINGS A5). I1, I4
# and I5 guard the rule's own bars, so a hit showing them anywhere else must still turn the job red.

I1, I4, I5 = "I1_first_seen_in_context_eligible", "I4_too_few_channels_eligible", "I5_too_young_eligible"
TWIN_CASE = "first-sorting-twin"
TWIN_FAILING = {TWIN_CASE: [I1, I4, I5]}


def _twin_entry():
    return known.Entry(TWIN_CASE, (I1, I4, I5), "A5", "Inesh", only_with="shared_id")


def test_a_hit_with_no_shared_id_is_not_known_to_a_shared_id_entry():
    v = known.judge([_twin_entry()], failing=TWIN_FAILING, hits=[known.Hit((I4,), shared_id=False)])
    assert v.red and v.known_hits == 0 and any(I4 in p and "new search hit" in p for p in v.problems)
    assert not known.holds([_twin_entry()], known.Hit((I4,), shared_id=False))


def test_the_same_hit_with_a_shared_id_is_known():
    v = known.judge([_twin_entry()], failing=TWIN_FAILING, hits=[known.Hit((I1, I4, I5), shared_id=True)])
    assert not v.red and v.known_hits == 1
    assert known.holds([_twin_entry()], known.Hit((I1, I4, I5), shared_id=True))


def test_a_hit_that_says_nothing_of_its_world_is_not_known_to_a_shared_id_entry():
    v = known.judge([_twin_entry()], failing=TWIN_FAILING, hits=[[I5]])     # plain labels: no shared id claimed
    assert v.red and v.known_hits == 0


def test_an_entry_without_the_condition_is_unaffected():
    failing = {"near-miss-unanchored": ["M_lookalike_helped", "M_shared_id_helped"]}
    hits = [known.Hit(("M_shared_id_helped",), shared_id=False), known.Hit(("M_shared_id_helped",), shared_id=True),
            ["M_shared_id_helped"]]
    v = known.judge([A5], failing=failing, hits=hits)
    assert not v.red and v.known_hits == 3


def test_each_label_is_held_by_its_own_entry():
    failing = {**TWIN_FAILING, "near-miss-unanchored": ["M_lookalike_helped", "M_shared_id_helped"]}
    mixed = known.Hit(("M_shared_id_helped", I4), shared_id=False)
    v = known.judge([A5, _twin_entry()], failing=failing, hits=[mixed])
    assert v.red and any(I4 in p for p in v.problems) and not any("M_shared_id_helped" in p for p in v.problems)
    both = known.judge([A5, _twin_entry()], failing=failing, hits=[known.Hit(mixed.labels, shared_id=True)])
    assert not both.red and both.known_hits == 1


def test_a_frozen_case_is_judged_as_before_under_the_condition():
    held = known.judge([_twin_entry()], failing=TWIN_FAILING)
    assert not held.red and held.known == [TWIN_CASE]
    stopped = known.judge([_twin_entry()], failing={TWIN_CASE: [I1, I4]})
    assert stopped.red and any(I5 in p and "take it off the list" in p for p in stopped.problems)
    extra = known.judge([_twin_entry()], failing={TWIN_CASE: [I1, I4, I5, "I7_near_miss_eligible"]})
    assert extra.red and any("I7_near_miss_eligible" in p for p in extra.problems)


def test_the_list_reads_the_condition_and_refuses_an_unknown_one(tmp_path):
    entry = {"case": "x", "labels": ["L"], "finding": "A5", "owner": "Inesh"}
    path = tmp_path / "known.json"
    path.write_text(json.dumps({"lab-fuzz": [entry, {**entry, "case": "y", "only_with": "shared_id"}]}))
    plain, scoped = known.load(path)["lab-fuzz"]
    assert plain.only_with is None and scoped.only_with == "shared_id"
    path.write_text(json.dumps({"lab-fuzz": [{**entry, "only_with": "sharedid"}]}))
    with pytest.raises(ValueError, match="only_with"):
        known.load(path)


def test_only_the_first_sorting_twin_entry_is_scoped_to_shared_ids():
    entries = known.load(ROOT / "evals" / "known-failures.json")["lab-fuzz"]
    scoped = [e for e in entries if e.only_with]
    assert [(e.labels, e.only_with) for e in scoped] == [((I1, I4, I5), "shared_id")]


def test_a_search_hit_says_whether_its_world_has_a_shared_id():
    from dialback_evals.cli import _search_hit
    from dialback_evals.worldgen import NumberSpec, SightingSpec, make_world
    number = "+13035550169"
    plain = make_world(numbers=[NumberSpec(number, ["contract"], 400)])
    shared = make_world(numbers=[NumberSpec(number, [], 0, sightings=(SightingSpec("contract", 400, shared_id=True),))])
    assert _search_hit([I4], plain) == known.Hit((I4,), shared_id=False)
    assert _search_hit([I4], shared) == known.Hit((I4,), shared_id=True)
