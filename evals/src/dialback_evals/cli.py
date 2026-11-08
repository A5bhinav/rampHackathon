"""The lab run CLI (spec 0017, Task 6).

`lab run --split voice` loads the scenarios, runs each through the real call and the real decision,
writes `results.jsonl` and `scorecard.json`, prints a one-line summary, and exits non-zero when any
hard gate fails. Deterministic (ScriptedAgent via LabAgent, scripted callers), so CI is reproducible
and needs no key. `--max-usd` is accepted for forward compatibility; the scripted slice never spends."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from dialback_decision import decide

from .lab_agent import LabAgent
from .runner import build_facts, run_scenario
from .scenario import Scenario, load_scenarios
from .scorers import ScenarioResult, score, scorecard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a scenario split and score it")
    run.add_argument("--split", default="voice")
    run.add_argument("--scenarios-root", default="evals/scenarios")
    run.add_argument("--out", default="evals/out")
    run.add_argument("--max-usd", type=float, default=0.0)
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(Path(args.scenarios_root) / args.split, Path(args.out))
    return 2


def _run(scenarios_dir: Path, out_dir: Path) -> int:
    results = [_score_one(s) for s in load_scenarios(scenarios_dir)]
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for res in results:
            fh.write(json.dumps(asdict(res)) + "\n")
    card = scorecard(results)
    (out_dir / "scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    print(f"lab: {card['passed']}/{card['total']} passed, {card['failed']} hard-gate failure(s)"
          + (f" -> {card['hard_gate_failures']}" if card["failed"] else ""))
    return 1 if card["failed"] else 0


def _score_one(s: Scenario) -> ScenarioResult:
    r = run_scenario(s, LabAgent(), clock=_clock())
    return score(s, r, decide(build_facts(s, r)))


def _clock():
    box = {"t": dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)}

    def now() -> dt.datetime:
        box["t"] += dt.timedelta(seconds=10)
        return box["t"]
    return now


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
