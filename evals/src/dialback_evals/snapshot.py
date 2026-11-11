"""`lab snapshot` (spec 0101): one real run, written as the file the scorecard page lays out.

It runs the voice scenarios the way `lab run` does and the archive search the way `lab fuzz --search
archive` does, and keeps only ids, rule names and world indexes. It writes no finding and freezes
nothing, so it never touches `evals/corpus-generated`. The page computes nothing: what it shows is
what this wrote, with the commit it ran on."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .guided import search
from .scenario import load_scenarios

NOT_MET = "NR99_conditions_not_met"   # says only that conditions weren't met; the next rule says which


def deciding_rule(outcome: str, rules_fired: list[str]) -> str | None:
    """The rule worth naming for a call: none for a verified one, which met them all."""
    if outcome == "verified":
        return None
    return next((rule for rule in rules_fired if rule != NOT_MET), None)


def build(scenarios_dir: Path, *, examples: int, seed: int, commit: str) -> dict[str, Any]:
    from .cli import _score_one  # imported here because cli imports this module
    results = [_score_one(s) for s in load_scenarios(scenarios_dir)]
    arc = search("archive", budget=examples, seed=seed)
    hits: dict[str, list[int]] = {}
    for index, labels in enumerate(arc.labels):
        for label in labels:
            hits.setdefault(label, []).append(index)
    return {
        "generated_by": "uv run lab snapshot",
        "commit": commit,
        "voice": {
            "total": len(results),
            "passed": sum(r.passed for r in results),
            "scenarios": [{"id": r.scenario_id, "class": r.scenario_class, "attack": r.is_attack, "outcome": r.outcome,
                           "passed": r.passed, "rule": deciding_rule(r.outcome, r.rules_fired),
                           "declared": list(r.declared)} for r in results],
        },
        "fuzz": {"seed": seed, "budget": examples, "worlds": len(arc.worlds), "hits": hits},
    }


def head(repo: Path) -> str:
    """The short commit this run is on, for the page to state."""
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def write(doc: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
