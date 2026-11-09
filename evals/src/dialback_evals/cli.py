"""The lab run CLI (spec 0017, Task 6).

`lab run --split voice` loads the scenarios, runs each through the real call and the real decision,
writes `results.jsonl` and `scorecard.json`, prints a one-line summary, and exits non-zero when any
hard gate fails. Deterministic (the production ScriptedAgent, scripted callers), so CI is reproducible
and needs no key. `--max-usd` is accepted for forward compatibility; the scripted slice never spends."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from dialback_decision import decide
from dialback_voice.agent import ScriptedAgent

from .oracle import required
from .runner import build_facts, run_scenario
from .scenario import Scenario, expected_hash, load_scenarios
from .scorers import ScenarioResult, score, scorecard

_REPO = Path(__file__).resolve().parents[3]   # evals/src/dialback_evals/cli.py -> the repo root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a scenario split and score it")
    run.add_argument("--split", default="voice")
    run.add_argument("--scenarios-root", default=str(_REPO / "evals" / "scenarios"))
    run.add_argument("--out", default=str(_REPO / "evals" / "out"))
    run.add_argument("--max-usd", type=float, default=0.0)
    freeze = sub.add_parser("freeze", help="record each scenario's hash and required outcome (expected.lock.json)")
    freeze.add_argument("--split", default="voice")
    freeze.add_argument("--scenarios-root", default=str(_REPO / "evals" / "scenarios"))
    args = parser.parse_args(argv)
    if args.command == "freeze":
        return _freeze(Path(args.scenarios_root) / args.split)
    if args.command == "run":
        attacker = os.environ.get("DIALBACK_LAB_ATTACKER", "scripted").strip().lower() or "scripted"
        if attacker != "scripted" or args.max_usd > 0:
            print("lab: only the scripted attacker exists, and it spends nothing: refusing "
                  f"DIALBACK_LAB_ATTACKER={attacker} --max-usd {args.max_usd}")
            return 2
        return _run(Path(args.scenarios_root) / args.split, Path(args.out))
    return 2


def _run(scenarios_dir: Path, out_dir: Path) -> int:
    scenarios = load_scenarios(scenarios_dir)
    if not scenarios:
        print(f"lab: no scenarios in {scenarios_dir}: nothing would be tested")
        return 2
    if drift := _drift(scenarios_dir, scenarios):
        print(f"lab: {drift}; review the change, then run `lab freeze` to accept it")
        return 2
    scored = [(s, _score_one(s)) for s in scenarios]
    results = [res for _, res in scored]
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for s, res in scored:
            fh.write(json.dumps({**asdict(res), "scenario_hash": expected_hash(s)}) + "\n")
    card = scorecard(results)
    card["scenarios_hash"] = _scenarios_hash(scenarios)       # the frozen scenario set this run attests to
    (out_dir / "scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    print(f"lab: {card['passed']}/{card['total']} passed, {card['failed']} hard-gate failure(s)"
          + (f" -> {card['hard_gate_failures']}" if card["failed"] else ""))
    return 1 if card["failed"] else 0


LOCK = "expected.lock.json"


def _expectations(scenarios: list[Scenario]) -> dict[str, dict]:
    """Each scenario's content hash and the outcome the oracle requires of it, as JSON would hold them."""
    return json.loads(json.dumps({s.id: {"hash": expected_hash(s), "required": asdict(required(s))}
                                  for s in scenarios}, sort_keys=True))


def _freeze(scenarios_dir: Path) -> int:
    scenarios = load_scenarios(scenarios_dir)
    if not scenarios:
        print(f"lab: no scenarios in {scenarios_dir}: nothing to freeze")
        return 2
    (scenarios_dir / LOCK).write_text(json.dumps(_expectations(scenarios), indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")
    print(f"lab: froze {len(scenarios)} scenario(s) in {scenarios_dir / LOCK}")
    return 0


def _drift(scenarios_dir: Path, scenarios: list[Scenario]) -> str | None:
    """Why the scenarios no longer match what was frozen, or None when they do (review fix F8)."""
    path = scenarios_dir / LOCK
    if not path.is_file():
        return f"no {LOCK} in {scenarios_dir}"
    frozen, now = json.loads(path.read_text(encoding="utf-8")), _expectations(scenarios)
    changed = sorted(i for i in set(frozen) | set(now) if frozen.get(i) != now.get(i))
    return f"scenario(s) changed, added or removed since the freeze: {changed}" if changed else None


def _score_one(s: Scenario) -> ScenarioResult:
    r = run_scenario(s, ScriptedAgent(), clock=_clock())
    return score(s, r, decide(build_facts(s, r)))


def _scenarios_hash(scenarios: list[Scenario]) -> str:
    return hashlib.sha256("".join(sorted(expected_hash(s) for s in scenarios)).encode()).hexdigest()


def _clock():
    box = {"t": dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)}

    def now() -> dt.datetime:
        box["t"] += dt.timedelta(seconds=10)
        return box["t"]
    return now


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
