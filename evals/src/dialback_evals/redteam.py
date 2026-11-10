"""Spec 0046, the measurement: does steering by `gain` or `gain_new` find planted breaks of select()
that the same search without steering misses? `lab redteam-bench` runs it.

The planted breaks are one-line edits of select.py, written into `evals/redteam/breaks.json` by a
session that never saw the score. The file pins the select.py they were written against (its commit
and sha256); the bench loads the real rule and every broken one from that exact source, so a later fix
to select.py never changes the measurement. Before anything runs it checks that every change to
breaks.json is an ancestor of the first commit that adds `guided.py` (the score), that every break's
line and text match, that the broken module loads, and that its witness tells the two rules apart.

Each arm (`archive`, `guided`, `guided-new`) searches `BUDGET` worlds at each seed against each broken
rule. A run finds a break when some world it evaluated shows a violation label on the broken rule that
the pinned real rule does not show on that same world. The real-rule runs are measurement: outside the
budget, the same for every arm. Per seed, a guided arm's primary metric is the number of `helps` breaks
it finds minus the number `archive` finds; it passes when a one-sided exact sign test over the seeds
where the two differ gives p <= 0.025 with at least 6 such seeds, and its guard holds when, on `defense`
breaks, it loses on no more seeds than it wins. The scores, the budget, the seeds and the bars are fixed
by the spec; this module only computes them.
"""
from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import re
import subprocess
import sys
import types
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import hypothesis

from .gencorpus import _dump, _load, checked, rerun
from .guided import open_moves, search

REPO = Path(__file__).resolve().parents[3]   # evals/src/dialback_evals/redteam.py -> the repo root
BREAKS = "evals/redteam/breaks.json"
SELECT = "packages/ledger/src/dialback_ledger/select.py"
GUIDED = "evals/src/dialback_evals/guided.py"
FROZEN = "evals/corpus-generated"
BUDGET = 1000
DEV_SEEDS = range(10)                        # for debugging the bench; every other seed is a result seed
ARMS = ("archive", "guided", "guided-new")
GUIDED_ARMS = ARMS[1:]
BREAK_KINDS = ("defense", "helps")
ALPHA = 0.025                                # 0.05 split across the two guided arms
MIN_DIFFERING = 6


class Refused(Exception):
    """The bench will not run; the message says why."""


# ---- the pinned rule and the breaks ----------------------------------------------------------------

def load_rule(source: str, name: str) -> types.ModuleType:
    """A copy of select.py, from its source text, as a module inside the ledger package, so its
    relative import of the lookalike classifier resolves to the installed one."""
    module_name = "dialback_ledger._bench_" + re.sub(r"\W", "_", name)
    module = types.ModuleType(module_name)
    module.__package__ = "dialback_ledger"     # no __file__: Hypothesis never mines a copy for constants
    sys.modules[module_name] = module           # dataclasses look a class's module up here
    try:
        exec(compile(source, f"<select.py: {name}>", "exec"), module.__dict__)  # noqa: S102  select.py, sha-pinned
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pinned_source(spec: dict, repo: Path = REPO) -> str:
    """The select.py breaks.json was written against: from its commit when the repository has it, or
    else the working tree's file if it is byte-identical (CI's shallow checkout has no old commits)."""
    commit, want = spec["select_commit"], spec["select_sha256"]
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{SELECT}"], capture_output=True, check=False)
    if shown.returncode == 0:
        if _sha(shown.stdout) != want:
            raise Refused(f"select.py at {commit[:12]} does not match the select_sha256 breaks.json records")
        return shown.stdout.decode("utf-8")
    path = Path(repo) / SELECT
    if path.is_file() and _sha(path.read_bytes()) == want:
        return path.read_text(encoding="utf-8")
    raise Refused(f"the pinned select.py is not available: commit {commit[:12]} is not in {repo}, and the "
                  "working tree's select.py has a different sha256")


def broken_source(source: str, brk: dict) -> str:
    """The pinned source with the break's one line replaced, after checking its line number and text."""
    lines, n, bid = source.split("\n"), brk.get("line"), brk.get("id")
    if not isinstance(n, int) or not 1 <= n <= len(lines) or lines[n - 1] != brk.get("old"):
        raise Refused(f"break {bid}: line {n} of the pinned select.py is not its `old` text")
    new = brk.get("new")
    if not isinstance(new, str) or "\n" in new:
        raise Refused(f"break {bid}: `new` must be a single line")
    lines[n - 1] = new
    return "\n".join(lines)


def _behaviour(rule: types.ModuleType, world) -> tuple:
    try:
        return ("output", rule.select(**world.kwargs))
    except Exception as error:  # noqa: BLE001  a crash is a behaviour too, different from any output
        return ("crash", type(error).__name__)


def validate(spec: dict, source: str) -> tuple[types.ModuleType, dict[str, types.ModuleType]]:
    """The real rule and each broken one, loaded. A break is refused, by id, when its line number or
    text does not match, its kind is not one the bench scores, its module does not compile or load, or
    its witness gives the same select() behaviour on both rules."""
    real = load_rule(source, "pinned")
    broken: dict[str, types.ModuleType] = {}
    for brk in spec.get("breaks", []):
        bid = brk.get("id")
        if not bid or bid in broken:
            raise Refused(f"break {bid!r}: every break needs an id of its own")
        if brk.get("kind") not in BREAK_KINDS:
            raise Refused(f"break {bid}: kind {brk.get('kind')!r} is not one of {', '.join(BREAK_KINDS)}")
        text = broken_source(source, brk)
        try:
            module = load_rule(text, bid)
        except Exception as error:
            raise Refused(f"break {bid}: the broken select.py does not compile or load "
                          f"({type(error).__name__}: {error})") from error
        try:
            witness = _load(brk["witness"])
        except Exception as error:
            raise Refused(f"break {bid}: its witness is not a world ({type(error).__name__}: {error})") from error
        if _behaviour(real, witness) == _behaviour(module, witness):
            raise Refused(f"break {bid}: its witness shows no difference between the real and the broken rule")
        broken[bid] = module
    if not broken:
        raise Refused("breaks.json holds no breaks")
    return real, broken


def check_commit_order(repo: Path = REPO) -> None:
    """Refuse unless every commit that changes breaks.json comes strictly before the first commit that
    adds guided.py, and breaks.json has no uncommitted change. Needs the full history."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)

    if git("rev-parse", "--is-shallow-repository").stdout.strip() != "false":
        raise Refused(f"{repo} is not a git repository with full history (a shallow clone hides the commit order)")
    if git("status", "--porcelain", "--", BREAKS).stdout.strip():
        raise Refused(f"{BREAKS} has uncommitted changes, made after guided.py was added")
    changes = git("log", "--format=%H", "--", BREAKS).stdout.split()
    if not changes:
        raise Refused(f"{BREAKS} was never committed")
    adds = git("log", "--diff-filter=A", "--format=%H", "--", GUIDED).stdout.split()
    if not adds:
        raise Refused(f"{GUIDED} is not committed, so the breaks cannot be shown to come before the score")
    first = adds[-1]                             # git log lists the newest first
    for commit in changes:
        if commit == first or git("merge-base", "--is-ancestor", commit, first).returncode != 0:
            raise Refused(f"breaks.json changed in {commit[:12]}, which is not before {first[:12]}, the first "
                          "commit that adds guided.py")


# ---- one run ---------------------------------------------------------------------------------------

_LOADED: dict[str, types.ModuleType] = {}       # per process: rule modules by the sha256 of their source


def _rule(source: str) -> types.ModuleType:
    key = _sha(source.encode("utf-8"))
    if key not in _LOADED:
        _LOADED[key] = load_rule(source, key[:16])
    return _LOADED[key]


def first_found(broken: Sequence[Sequence[str]], real: Sequence[Sequence[str]] | Callable[[int], Sequence[str]]
                ) -> int | None:
    """The index of the first world whose labels on the broken rule include one the real rule does not
    show on that world, or None. `real` is only consulted for worlds the broken rule labels."""
    real_of = real if callable(real) else real.__getitem__
    for i, labels in enumerate(broken):
        if labels and set(labels) - set(real_of(i)):
            return i
    return None


def real_run(source: str, *, seed: int, budget: int, new_moves: Sequence[str]) -> dict:
    """The archive search on the pinned real rule: the labels of its worlds (the real-rule cache for
    every arm at this seed) and how often `gain` and `gain_new` reach 1 there."""
    arc = search("archive", budget=budget, seed=seed, rule=_rule(source), new_moves=new_moves)
    return {"seed": seed, "calls": arc.calls, "worlds": len(arc.worlds),
            "labels": dict(zip(arc.digests, arc.labels, strict=True)),
            "gain": sum(g >= 1 for g in arc.gains), "gain_new": sum(g >= 1 for g in arc.gains_new)}


def run_arm(source: str, brk: dict, *, seed: int, arm: str, budget: int, new_moves: Sequence[str],
            real_labels: dict[str, list[str]] | None = None) -> dict:
    """One arm against one broken rule at one seed, and whether it found the break."""
    real, broken = _rule(source), _rule(broken_source(source, brk))
    arc = search(arm, budget=budget, seed=seed, rule=broken, new_moves=new_moves)
    cache = dict(real_labels or {})
    extra = [0]

    def real_of(i: int) -> list[str]:
        digest = arc.digests[i]
        if digest not in cache:
            cache[digest] = checked(arc.worlds[i], real)
            extra[0] += 1
        return cache[digest]

    at = first_found(arc.labels, real_of)
    return {"break": brk["id"], "kind": brk.get("kind"), "seed": seed, "arm": arm, "calls": arc.calls,
            "worlds": len(arc.worlds), "worlds_with_labels": sum(bool(labels) for labels in arc.labels),
            "found_at": at,
            "found_labels": None if at is None else sorted(set(arc.labels[at]) - set(real_of(at))),
            "found_world": None if at is None else _dump(arc.worlds[at]),
            "real_rule_runs": extra[0], "anomalies": len(arc.anomalies)}


def _call(job: tuple) -> dict:
    fn, args, kwargs = job
    return fn(*args, **kwargs)


def _measure(source: str, breaks: list[dict], seeds: list[int], budget: int, moves: Sequence[str],
             jobs: int) -> tuple[dict[int, dict], list[dict]]:
    """Every seed's real-rule run, then every (seed, arm, break) run with that seed's real labels."""
    total = len(seeds) * (1 + len(ARMS) * len(breaks))
    done = [0]

    def tick() -> None:
        done[0] += 1
        print(f"lab redteam-bench: {done[0]}/{total} runs done", file=sys.stderr, flush=True)

    def arm_jobs(seed: int, labels: dict) -> list[tuple]:
        return [(run_arm, (source, brk), {"seed": seed, "arm": arm, "budget": budget, "new_moves": moves,
                                          "real_labels": labels}) for arm in ARMS for brk in breaks]

    reals: dict[int, dict] = {}
    runs: list[dict] = []
    if jobs <= 1:
        for seed in seeds:
            reals[seed] = real_run(source, seed=seed, budget=budget, new_moves=moves)
            tick()
            for job in arm_jobs(seed, reals[seed]["labels"]):
                runs.append(_call(job))
                tick()
        return reals, runs
    with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
        pending = {pool.submit(_call, (real_run, (source,), {"seed": s, "budget": budget, "new_moves": moves})): ("real", s)
                   for s in seeds}
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                what, seed = pending.pop(future)
                result = future.result()
                tick()
                if what == "real":
                    reals[seed] = result
                    pending |= {pool.submit(_call, job): ("arm", seed) for job in arm_jobs(seed, result["labels"])}
                else:
                    runs.append(result)
    return reals, runs


# ---- the bar, the guard, the kill rule -------------------------------------------------------------

def sign_test(wins: int, losses: int) -> float:
    """One-sided exact sign test: the chance of at least `wins` wins in wins + losses fair coin flips."""
    n = wins + losses
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / 2 ** n if n else 1.0


def bar(diffs: Sequence[int]) -> dict:
    """The pass bar on per-seed differences (guided minus archive): p <= ALPHA over the differing seeds,
    with at least MIN_DIFFERING of them; fewer is no evidence, which fails."""
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    p = sign_test(wins, losses)
    return {"wins": wins, "losses": losses, "ties": len(diffs) - wins - losses, "differing": wins + losses,
            "p": p, "alpha": ALPHA, "passed": wins + losses >= MIN_DIFFERING and p <= ALPHA}


def guard(diffs: Sequence[int]) -> dict:
    """On defense breaks the guided arm loses on no more seeds than it wins."""
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    return {"wins": wins, "losses": losses, "passed": losses <= wins}


def keep(bars: dict[str, bool], guards: dict[str, bool], totals: dict[str, int]) -> str | None:
    """The kill rule: an arm failing its bar or its guard is dropped; of two survivors the larger total
    primary metric is kept, and guided-new on a tie."""
    alive = [arm for arm in GUIDED_ARMS if bars[arm] and guards[arm]]
    if len(alive) < 2:
        return alive[0] if alive else None
    return "guided" if totals["guided"] > totals["guided-new"] else "guided-new"


def summarize(breaks: list[dict], seeds: list[int], reals: dict[int, dict], runs: list[dict]) -> dict:
    kinds = {b["id"]: b["kind"] for b in breaks}
    first = {bid: {arm: {str(s): None for s in seeds} for arm in ARMS} for bid in kinds}
    per_seed: dict[str, dict] = {}
    for seed in seeds:
        for arm in ARMS:
            mine = [r for r in runs if r["seed"] == seed and r["arm"] == arm]
            found = sorted(r["break"] for r in mine if r["found_at"] is not None)
            worlds = [r["worlds"] for r in mine]
            per_seed.setdefault(str(seed), {})[arm] = {
                "helps": sum(kinds[b] == "helps" for b in found), "defense": sum(kinds[b] == "defense" for b in found),
                "found": found, "calls": min((r["calls"] for r in mine), default=0),
                "worlds": {"min": min(worlds, default=0), "max": max(worlds, default=0),
                           "mean": sum(worlds) / len(worlds) if worlds else 0},
                "runs": {r["break"]: {k: r[k] for k in ("found_at", "found_labels", "worlds", "calls",
                                                        "real_rule_runs", "anomalies")} for r in mine}}
            for r in mine:
                first[r["break"]][arm][str(seed)] = r["found_at"]
    primary, sign_tests, guards, totals = {}, {}, {}, {}
    for arm in GUIDED_ARMS:
        helps = [per_seed[str(s)][arm]["helps"] - per_seed[str(s)]["archive"]["helps"] for s in seeds]
        defense = [per_seed[str(s)][arm]["defense"] - per_seed[str(s)]["archive"]["defense"] for s in seeds]
        primary[arm] = {"per_seed": helps, "total": sum(helps)}
        sign_tests[arm], guards[arm], totals[arm] = bar(helps), guard(defense), sum(helps)
    kept = keep({a: sign_tests[a]["passed"] for a in GUIDED_ARMS}, {a: guards[a]["passed"] for a in GUIDED_ARMS},
                totals)
    worlds = sum(r["worlds"] for r in reals.values())
    return {
        "per_seed": per_seed, "primary": primary, "sign_tests": sign_tests, "guards": guards, "kept": kept,
        "first_found": first,
        "never_found": sorted(b for b in kinds if all(v is None for arm in ARMS for v in first[b][arm].values())),
        "not_found_per_seed": {str(s): sorted(b for b in kinds if all(first[b][arm][str(s)] is None for arm in ARMS))
                               for s in seeds},
        "gain_share_on_pinned_rule": {
            "seeds": seeds, "worlds": worlds,
            "gain": sum(r["gain"] for r in reals.values()) / worlds if worlds else 0.0,
            "gain_new": sum(r["gain_new"] for r in reals.values()) / worlds if worlds else 0.0,
            "per_seed": {str(s): {k: reals[s][k] for k in ("worlds", "gain", "gain_new")} for s in seeds}},
        "anomalies": sum(r["anomalies"] for r in runs),
    }


def parse_seeds(text: str) -> list[int]:
    """`0-9`, `1000-1009`, `0,3,5-7`: the seeds in order, each once."""
    seeds: list[int] = []
    for part in text.split(","):
        lo, sep, hi = part.strip().partition("-")
        first, last = int(lo), int(hi) if sep else int(lo)
        if last < first:
            raise ValueError(f"empty seed range {part!r}")
        seeds += range(first, last + 1)
    return list(dict.fromkeys(seeds))


def result_seeds(seeds: Sequence[int]) -> list[int]:
    """The seeds that are not dev seeds: they run only with --held-out."""
    return [s for s in seeds if s not in DEV_SEEDS]


def bench(seeds: Sequence[int], *, held_out: bool, out: Path, repo: Path = REPO, jobs: int = 1,
          budget: int = BUDGET) -> dict:
    """Run the measurement and write `out`. Refuses seeds outside 0 to 9 without `held_out`, a held-out
    run at any budget but the spec's, a breaks.json changed after guided.py was added, and any invalid
    break. `budget` exists for the tests; the CLI never changes it."""
    seeds = list(dict.fromkeys(seeds))
    if (outside := result_seeds(seeds)) and not held_out:
        raise Refused(f"seeds {outside} are held-out seeds, which run once, deliberately, with --held-out; "
                      "debug the bench on dev seeds 0 to 9")
    if held_out and budget != BUDGET:
        raise Refused(f"a held-out run uses the spec's budget of {BUDGET} worlds, not {budget}")
    repo = Path(repo)
    check_commit_order(repo)
    spec = json.loads((repo / BREAKS).read_text(encoding="utf-8"))
    source = pinned_source(spec, repo)
    real, _ = validate(spec, source)
    failing = rerun(repo / FROZEN, rule=real)   # guided-new's moves: the frozen cases, re-run on the pinned rule
    moves = open_moves(failing)
    reals, runs = _measure(source, spec["breaks"], seeds, budget, moves, jobs)
    report = {"spec": "0046", "hypothesis": hypothesis.__version__, "seeds": seeds, "held_out": held_out,
              "budget": budget, "select_commit": spec["select_commit"], "select_sha256": spec["select_sha256"],
              "breaks": {b["id"]: b["kind"] for b in spec["breaks"]}, "guided_new_moves": list(moves),
              "frozen_failing": [[case, labels] for case, labels in failing],
              **summarize(spec["breaks"], seeds, reals, runs)}
    report = json.loads(json.dumps(report))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
