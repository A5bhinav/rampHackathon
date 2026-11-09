"""Spec 0033, Task 3: a violating world is frozen as a named case, a frozen case round-trips exactly,
and every case the lab has frozen is re-run against the real rule (a found case stays found)."""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
from dialback_evals.gencorpus import freeze, fuzz, load_all, load_world, rerun
from dialback_evals.worldgen import NumberSpec, SightingSpec, make_world, run

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "evals" / "corpus-generated"
FICTIONAL = re.compile(r"^\+1\d{3}55501\d{2}$")   # NANP 555-0100 to 555-0199, reserved for fiction
E164 = re.compile(r"\+1\d{10}")
HARD = NumberSpec("+14695550199", [], 0, is_poison=True, sightings=(
    SightingSpec("email", 30, sender="lookalike", recorded_late=True),
    SightingSpec("listing", 200, visibility="tiny", shared_id=True),
    SightingSpec("email", 90, sender="subdomain", subject="re")))


def _round_trips(back, w) -> bool:
    """Did a reload give back the same world? Compared field for field on the inputs, not on the rule's
    output, which drops late records before it hashes (review finding I3)."""
    return back.kwargs == w.kwargs and back.poison == w.poison and back.settings == w.settings


def test_frozen_world_round_trips(tmp_path):
    for i, present in enumerate((True, False)):   # with and without a change request
        w = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500), HARD],
                       request_present=present)
        case = freeze(w, f"round-trip-{i}", tmp_path, labels=["example"], why="a round trip")
        assert _round_trips(load_world(case), w)


def test_round_trip_check_sees_a_lost_late_record():
    # Review finding I3: the rule drops late records before it hashes, so comparing outputs cannot see
    # a reload that lost one, which is the very field world.json exists to keep.
    w = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500), HARD])
    opened = w.kwargs["case_opened_at"]
    lossy = dataclasses.replace(w, kwargs={**w.kwargs, "documents": [d for d in w.kwargs["documents"]
                                                                     if d.recorded_at < opened]})
    assert run(lossy) == run(w)                    # the rule itself cannot tell the two apart
    assert not _round_trips(lossy, w)              # the round-trip check must


def test_freeze_never_replaces_a_different_world(tmp_path):
    # Review finding I2: a frozen case is evidence; nothing may overwrite it with another world.
    a = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500)])
    b = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 400)])
    freeze(a, "case", tmp_path, labels=["first"])
    freeze(a, "case", tmp_path, labels=["refreshed"])        # the same world again only refreshes its notes
    with pytest.raises(FileExistsError):
        freeze(b, "case", tmp_path)
    assert load_world(tmp_path / "case").kwargs == a.kwargs


def test_fuzz_names_a_case_by_its_content(tmp_path, monkeypatch):
    from dialback_evals.gencorpus import world_digest
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["M_reorder"])
    fuzz(examples=25, seed=0, out_dir=tmp_path)
    [(label, world)] = load_all(tmp_path)
    assert label == f"fuzz-seed0-m-reorder-{world_digest(world)[:8]}"


def test_freeze_refuses_a_real_looking_number(tmp_path):
    # Review finding C1: a real-looking number must never reach a committed fixture, whatever produced it.
    real_looking = "+1201" + "5551234"   # built in pieces so the file scan below does not see it
    with pytest.raises(ValueError, match="real-looking"):
        freeze(make_world(numbers=[NumberSpec(real_looking, ["contract", "listing"], 500)]), "x", tmp_path)
    assert load_all(tmp_path) == []


def test_lab_files_hold_only_fictional_numbers():
    found = {}
    for path in [*(ROOT / "evals" / "src").rglob("*.py"), *(ROOT / "evals" / "tests").rglob("*.py"),
                 *GENERATED.rglob("*.json")]:
        bad = sorted({n for n in E164.findall(path.read_text(encoding="utf-8")) if not FICTIONAL.match(n)})
        if bad:
            found[str(path.relative_to(ROOT))] = bad
    assert found == {}, found


def test_fuzz_against_a_broken_rule_freezes_a_case(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["I7_poison_pick_below_bar"])
    assert fuzz(examples=25, seed=0, out_dir=tmp_path) == ["I7_poison_pick_below_bar"]
    assert len(load_all(tmp_path)) == 1


def test_fuzz_is_clean_when_nothing_violates(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: [])
    assert fuzz(examples=25, seed=0, out_dir=tmp_path) == []
    assert load_all(tmp_path) == []


def test_rerun_reports_frozen_cases_the_rule_still_fails(tmp_path, monkeypatch):
    # rerun()'s plumbing only. The baseline used to be the real rule passing a plain world; since the
    # Nov 8 gate (I12) the real rule fails every world with an eligible number, so a passing oracle is
    # stubbed here too. Whether the real rule passes the frozen cases is test_frozen_findings_rerun_against_the_rule.
    freeze(make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500)]), "plain", tmp_path)
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: [])
    assert rerun(tmp_path) == []
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["M_reorder"])
    assert rerun(tmp_path) == [("plain", ["M_reorder"])]


@pytest.mark.selection_gate
def test_frozen_findings_rerun_against_the_rule():
    failing = rerun(GENERATED)
    assert failing == [], failing   # each frozen finding must pass on the rule; red until select() is fixed
