"""`lab snapshot` (spec 0101): one real run of the voice scenarios and the fuzz search, written as the
file the scorecard page lays out. The page computes nothing, so these checks are what keep it honest."""
from __future__ import annotations

import json
import re
from pathlib import Path

from dialback_evals import snapshot
from dialback_evals.cli import _score_one, main
from dialback_evals.guided import search
from dialback_evals.scenario import load_scenarios
from dialback_evals.scorers import scorecard

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "evals" / "scenarios" / "voice"
EXAMPLES = 40


def _doc() -> dict:
    return snapshot.build(VOICE, examples=EXAMPLES, seed=0, commit="abc1234")


def test_the_voice_results_match_a_real_run():  # L1
    voice = _doc()["voice"]
    card = scorecard([_score_one(s) for s in load_scenarios(VOICE)])
    assert (voice["total"], voice["passed"]) == (card["total"], card["passed"])
    assert len(voice["scenarios"]) == card["total"]
    assert sorted(s["id"] for s in voice["scenarios"] if not s["passed"]) == sorted(card["hard_gate_failures"])
    assert {s["outcome"] for s in voice["scenarios"]} <= {"verified", "not_verified", "needs_review"}
    assert set(voice["scenarios"][0]) == {"id", "class", "attack", "outcome", "passed", "rule", "declared"}
    # A verdict resting on a fact production never computes measures a stand-in (spec 0048, A3), so the page marks it.
    assert {s["id"]: s["declared"] for s in voice["scenarios"] if s["declared"]} == \
        {d["scenario"]: d["declared"] for d in card["declared"]}


def test_the_deciding_rule_skips_the_not_met_marker():  # L1
    # NR99 only says "conditions not met"; the rule worth showing is the first one that was not met.
    assert snapshot.deciding_rule("needs_review", ["NR99_conditions_not_met", "V07_right_person_confirmed_unmet"]) == \
        "V07_right_person_confirmed_unmet"
    assert snapshot.deciding_rule("not_verified", ["NV02_digits_mismatch", "NV03_digits_match_old_account"]) == \
        "NV02_digits_mismatch"
    assert snapshot.deciding_rule("verified", ["V01_number_eligible"]) is None
    assert snapshot.deciding_rule("needs_review", []) is None


def test_the_fuzz_results_match_the_archive_search():  # L1
    fuzz = _doc()["fuzz"]
    arc = search("archive", budget=EXAMPLES, seed=0)
    assert (fuzz["seed"], fuzz["budget"], fuzz["worlds"]) == (0, EXAMPLES, len(arc.worlds))
    every = {label for labels in arc.labels for label in labels}
    assert set(fuzz["hits"]) == every
    for label, worlds in fuzz["hits"].items():
        assert worlds == [i for i, labels in enumerate(arc.labels) if label in labels]


def test_the_file_holds_no_number_digits_or_transcript():  # L2
    text = json.dumps(_doc())
    assert re.search(r"\+?\d{7,}", text) is None, "nothing long enough to be a phone or account number"
    assert "transcript" not in text and "account_last4" not in text


def test_the_committed_file_holds_no_number_digits_or_transcript():  # L2 (review fix)
    text = (ROOT / "apps" / "web" / "src" / "data" / "lab-results.json").read_text()
    assert re.search(r"\+?\d[\d\-.() ]{5,}\d", text) is None, "nothing shaped like a phone or account number"
    assert "transcript" not in text and "account_last4" not in text


def _listing(directory: Path) -> list[tuple[str, int]]:
    if not directory.exists():
        return []
    return sorted((str(p), p.stat().st_mtime_ns) for p in directory.rglob("*"))


def test_it_writes_only_its_own_file(tmp_path):  # L3
    frozen, out = ROOT / "evals" / "corpus-generated", ROOT / "evals" / "out"
    before = _listing(frozen), _listing(out)
    target = tmp_path / "lab-results.json"
    assert main(["snapshot", "--out", str(target), "--examples", str(EXAMPLES)]) == 0
    assert (_listing(frozen), _listing(out)) == before
    written = json.loads(target.read_text())
    assert set(written) == {"generated_by", "commit", "voice", "fuzz"}
    assert re.fullmatch(r"[0-9a-f]{7,12}", written["commit"])
