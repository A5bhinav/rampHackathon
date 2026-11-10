"""The lab CLI (spec 0017, Task 6; spec 0033, Task 5).

`lab run --split voice` loads the scenarios, runs each through the real call and the real decision,
writes `results.jsonl` and `scorecard.json`, prints a one-line summary, and exits non-zero when any
hard gate fails. Deterministic (the production ScriptedAgent, scripted callers), so CI is reproducible
and needs no key. `--max-usd` is accepted for forward compatibility; the scripted slice never spends.

`lab fuzz` re-runs every frozen finding under `--out`, then searches `--examples` generated worlds at
`--seed`, freezing the first shrunk violation there; it exits non-zero on any frozen case that still
fails or any new finding. `lab frontier` and `lab overblock` write the two measurement tables and exit
non-zero only when their stated asserts fail."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from dialback_voice.agent import ScriptedAgent

from .oracle import required
from .runner import facts_for, run_scenario
from .scenario import Scenario, expected_hash, load_scenarios
from .scorers import ScenarioResult, score, scorecard

# The fuzz lab's modules import the dev-only hypothesis package, so the commands that need them import
# them when they run: `lab run` starts without it (spec 0048, C4; FINDINGS B7, M2).

_REPO = Path(__file__).resolve().parents[3]   # evals/src/dialback_evals/cli.py -> the repo root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a scenario split and score it")
    run.add_argument("--split", default="voice")
    run.add_argument("--scenarios-root", default=str(_REPO / "evals" / "scenarios"))
    run.add_argument("--out", default=str(_REPO / "evals" / "out"))
    run.add_argument("--max-usd", type=float, default=0.0)
    fz = sub.add_parser("fuzz", help="re-run frozen findings, then search generated worlds (spec 0033)")
    fz.add_argument("--examples", type=int, default=2000)
    fz.add_argument("--seed", type=int, default=0)
    fz.add_argument("--out", default="evals/corpus-generated")
    fr = sub.add_parser("frontier", help="sweep the known-limit frontier (spec 0033)")
    fr.add_argument("--out", default="evals/out/frontier.json")
    ob = sub.add_parser("overblock", help="sweep single pieces of attacker noise (spec 0033)")
    ob.add_argument("--out", default="evals/out/overblock.json")
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
    if args.command == "fuzz":
        return _fuzz(args.examples, args.seed, Path(args.out))
    if args.command == "frontier":
        return _frontier(Path(args.out))
    if args.command == "overblock":
        return _overblock(Path(args.out))
    return 2


def _fuzz(examples: int, seed: int, out_dir: Path) -> int:
    from .gencorpus import fuzz, load_all, rerun
    before = {label for label, _ in load_all(out_dir)}
    failing = rerun(out_dir)
    for label, found in failing:
        print(f"lab fuzz: frozen case {label} still fails: {', '.join(found)}")
    new = fuzz(examples=examples, seed=seed, out_dir=out_dir)
    fresh = sorted({label for label, _ in load_all(out_dir)} - before) if new else []
    if new and fresh:
        print(f"lab fuzz: new finding ({', '.join(new)}) frozen as {', '.join(fresh)} under {out_dir}")
    elif new:
        print(f"lab fuzz: the search re-found an already-frozen case ({', '.join(new)})")
    print(f"lab fuzz: {len(failing)} frozen case(s) failing, {len(fresh)} new finding(s) "
          f"(seed {seed}, {examples} examples)")
    return 1 if failing or new else 0


def _write(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _frontier(out: Path) -> int:
    from .frontier import frontier_grid, frontier_table, sweep, threshold_holds
    from .worldgen import DEFAULT
    cells = sweep()
    table = frontier_table(cells)
    _write(out, table)
    print("lab frontier: is the poison number picked (P) or not (.), by counting channels and days of lead")
    for line in frontier_grid(cells):
        print(line)
    print(f"lab frontier: {table['min_effort']['summary']}")
    return 0 if threshold_holds(cells, DEFAULT) else 1


def _overblock(out: Path) -> int:
    from .frontier import overblock_holds, overblock_sweep, overblock_table
    cells = overblock_sweep()
    table = overblock_table(cells)
    _write(out, table)
    for entry in table["summary"].values():
        print(f"lab overblock: {entry['line']}")
    return 0 if overblock_holds(cells) else 1


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
    return score(s, r, facts_for(s, r))


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
