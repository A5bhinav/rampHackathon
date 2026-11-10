"""The lab CLI (spec 0017, Task 6; spec 0083, Task 5).

`lab run --split voice` loads the scenarios, runs each through the real call and the real decision,
writes `results.jsonl` and `scorecard.json`, prints a one-line summary, and exits non-zero when any
hard gate fails. Deterministic (the production ScriptedAgent, scripted callers), so CI is reproducible
and needs no key. `--max-usd` is accepted for forward compatibility; the scripted slice never spends.

`lab fuzz` re-runs every frozen finding under `--out`, then searches `--examples` generated worlds at
`--seed`, freezing the first shrunk violation there; it exits non-zero on any frozen case that still
fails or any new finding. `lab frontier` and `lab overblock` write the two measurement tables and exit
non-zero only when their stated asserts fail.

Spec 0046: `lab fuzz --search archive` runs the whole budget instead, freezes a 1-minimal world for
every label no frozen case records yet, and writes near misses to `--near-misses`, which never changes
the exit code; `--search first` (the default) is the search above, unchanged. Both hold lab-fuzz's
known-failures list (spec 0048, D3)."""
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
KNOWN_FAILURES = _REPO / "evals" / "known-failures.json"
_KNOWN_HELP = ("the strict known-failures list (spec 0048, D3): a path, 'none', or 'auto' (the repo's list when "
               "the command runs on the repo's own folder, none otherwise)")


def _known(choice: str, target: Path, repo_target: Path) -> Path | None:
    """Which known-failures list a run holds itself to, if any."""
    if choice == "none":
        return None
    if choice != "auto":
        return Path(choice)
    return KNOWN_FAILURES if Path(target).resolve() == repo_target.resolve() else None


def _held(job: str, path: Path | None, failing: dict, hits: list) -> int | None:
    """Judge a job's failures by its list and print the result: 1 when it is red, 0 when it holds, None when
    no list applies (the caller then fails on any failure)."""
    from . import known
    entries = known.for_job(path, job)
    if entries is None:
        return None
    verdict = known.judge(entries, failing, hits)
    findings = known.missing_findings(entries, (_REPO / "evals" / "FINDINGS.md").read_text(encoding="utf-8"))
    for line in known.report(job, verdict, entries, findings):
        print(line)
    return 1 if verdict.red or findings else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a scenario split and score it")
    run.add_argument("--split", default="voice")
    run.add_argument("--scenarios-root", default=str(_REPO / "evals" / "scenarios"))
    run.add_argument("--out", default=str(_REPO / "evals" / "out"))
    run.add_argument("--max-usd", type=float, default=0.0)
    run.add_argument("--known-failures", default="auto", help=_KNOWN_HELP)
    fz = sub.add_parser("fuzz", help="re-run frozen findings, then search generated worlds (spec 0083)")
    fz.add_argument("--examples", type=int, default=2000)
    fz.add_argument("--seed", type=int, default=0)
    fz.add_argument("--out", default="evals/corpus-generated")
    fz.add_argument("--known-failures", default="auto", help=_KNOWN_HELP)
    fz.add_argument("--search", choices=["first", "archive"], default="first",
                    help="first: stop at the first violation; archive: run the whole budget (spec 0046)")
    fz.add_argument("--near-misses", default="evals/out/near-misses.json",
                    help="where --search archive writes its near misses (never gated)")
    fr = sub.add_parser("frontier", help="sweep the known-limit frontier (spec 0083)")
    fr.add_argument("--out", default="evals/out/frontier.json")
    ob = sub.add_parser("overblock", help="sweep single pieces of attacker noise (spec 0083)")
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
        scenarios_dir = Path(args.scenarios_root) / args.split
        return _run(scenarios_dir, Path(args.out), _known(args.known_failures, scenarios_dir, _REPO / "evals" /
                                                          "scenarios" / "voice"))
    if args.command == "fuzz":
        listed = _known(args.known_failures, Path(args.out), _REPO / "evals" / "corpus-generated")
        if args.search == "first":
            return _fuzz(args.examples, args.seed, Path(args.out), listed)
        return _search(args.examples, args.seed, Path(args.out), Path(args.near_misses), listed)
    if args.command == "frontier":
        return _frontier(Path(args.out))
    if args.command == "overblock":
        return _overblock(Path(args.out))
    return 2


def _fuzz(examples: int, seed: int, out_dir: Path, known_failures: Path | None = None) -> int:
    from . import known
    from .gencorpus import fuzz, load_all, rerun
    before = {label for label, _ in load_all(out_dir)}
    failing = rerun(out_dir)
    for label, found in failing:
        print(f"lab fuzz: frozen case {label} still fails: {', '.join(found)}")
    entries = known.for_job(known_failures, "lab-fuzz")
    listed = {label for e in entries or () for label in e.labels}
    # A hit whose labels are all listed is counted, not frozen: frozen, it would fail as an unlisted case.
    new = fuzz(examples=examples, seed=seed, out_dir=out_dir,
               keep=lambda labels: entries is None or not set(labels) <= listed)
    fresh = sorted({label for label, _ in load_all(out_dir)} - before) if new else []
    if new and fresh:
        print(f"lab fuzz: new finding ({', '.join(new)}) frozen as {', '.join(fresh)} under {out_dir}")
    elif new:
        print(f"lab fuzz: the search re-found an already-frozen case ({', '.join(new)})")
    print(f"lab fuzz: {len(failing)} frozen case(s) failing, {len(fresh)} new finding(s) "
          f"(seed {seed}, {examples} examples)")
    held = _held("lab-fuzz", known_failures, dict(failing), [new] if new else [])
    return (1 if failing or new else 0) if held is None else held


def _search(examples: int, seed: int, out_dir: Path, near_misses: Path, known_failures: Path | None = None) -> int:
    """Spec 0046's search that keeps going. Every failing world it evaluates is a search hit for the
    known-failures list; the near misses are written and never change the exit code."""
    from . import known
    from .gencorpus import rerun
    from .guided import ALL_MOVES, archive_fuzz
    failing = rerun(out_dir)
    for label, found in failing:
        print(f"lab fuzz: frozen case {label} still fails: {', '.join(found)}")
    entries = known.for_job(known_failures, "lab-fuzz")
    listed = {label for e in entries or () for label in e.labels}
    result = archive_fuzz("archive", examples=examples, seed=seed, out_dir=out_dir, near_misses=near_misses,
                          counted=listed)
    arc = result.archive
    for label, hit in arc.first.items():
        where = (f"frozen as {result.frozen[label]} under {out_dir}" if label in result.frozen
                 else "counted, not frozen: a frozen case records it or the known-failures list holds it")
        print(f"lab fuzz: {label}, first at world {hit.index}: {where}")
    print(f"lab fuzz: {sum(m in arc.near for m in ALL_MOVES)} near miss(es) written to {result.near_misses} "
          "(they never change the exit code)")
    print(f"lab fuzz: {len(failing)} frozen case(s) failing, {len(arc.first)} label(s) found, "
          f"{len(result.frozen)} new case(s) (--search archive, seed {seed}, {examples} examples, "
          f"{len(arc.worlds)} distinct worlds)")
    held = _held("lab-fuzz", known_failures, dict(failing), [labels for labels in arc.labels if labels])
    return (1 if failing or arc.first else 0) if held is None else held


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


def _run(scenarios_dir: Path, out_dir: Path, known_failures: Path | None = None) -> int:
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
    held = _held("lab-smoke", known_failures, {r.scenario_id: r.failures for r in results if not r.passed}, [])
    return (1 if card["failed"] else 0) if held is None else held


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
