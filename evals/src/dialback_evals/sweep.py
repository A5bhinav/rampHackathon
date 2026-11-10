"""Spec 0048, A5: the rule-removal sweep (`lab sweep`).

Each of the decision table's rules (blocks 0 to 3; strict mode's S01 is not in the call path) is removed in
turn. The removal is **seen** when some voice scenario or generated call report (part B) gets a different
outcome than with the full table. Every rule must be seen, except the rules listed in
`evals/UNREACHABLE.md`, each with its reason: no honest call or generated report reaches the rule, or another
rule always gives the same outcome (reviewed by Abhi, who owns the rules; Q103).

The facts are built once per subject (a scenario through the real call and A1's chain, a report through
A1's chain) and only `decide()` is re-run per removal, since removing a rule changes nothing upstream. The
sweep also says how each rule is seen: by a scenario that rests only on computed facts, by one that rests on
a declared fact (A3), by a reachable report, or only by a contract-only report production never sends.
"""
from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from dialback_decision import decide, rules
from dialback_decision.facts import CaseFacts

BLOCKS = ("BLOCK0", "NOT_VERIFIED", "NEEDS_REVIEW", "VERIFIED")
_ROW = re.compile(r"^\|\s*`?([A-Z]+[0-9]{2}_[a-z_]+)`?\s*\|\s*(.*?)\s*\|\s*$")


def rule_ids() -> list[str]:
    return [r.id for block in BLOCKS for r in getattr(rules, block)]


@contextmanager
def without(rule_id: str) -> Iterator[None]:
    """The table with one rule removed, for the length of the block."""
    block = next(b for b in BLOCKS if any(r.id == rule_id for r in getattr(rules, b)))
    saved = getattr(rules, block)
    setattr(rules, block, tuple(r for r in saved if r.id != rule_id))
    try:
        yield
    finally:
        setattr(rules, block, saved)


@dataclasses.dataclass(frozen=True)
class Subject:
    name: str
    kind: str              # scenario | declared_scenario | reachable | contract
    facts: CaseFacts


def scenario_subjects(scenarios_dir: Path, clock: Callable) -> list[Subject]:
    from dialback_voice.agent import ScriptedAgent

    from .runner import facts_for, run_scenario
    from .scenario import load_scenarios
    out = []
    for s in load_scenarios(scenarios_dir):
        r = run_scenario(s, ScriptedAgent(), clock=clock())
        out.append(Subject(s.id, "declared_scenario" if s.declared else "scenario", facts_for(s, r)))
    return out


def report_subjects(examples: int, seed: int) -> list[Subject]:
    from .draws import sample
    from .reports import report_cases, run
    return [Subject(f"{domain} report {i}", domain, run(case).facts)
            for domain in ("reachable", "contract")
            for i, case in enumerate(sample(report_cases(domain), examples=examples, seed=seed))]


def sweep(subjects: list[Subject]) -> dict[str, list[Subject]]:
    """Each rule, and the subjects whose outcome changes when it is removed."""
    base = [decide(s.facts).outcome for s in subjects]
    seen = {}
    for rule_id in rule_ids():
        with without(rule_id):
            seen[rule_id] = [s for s, before in zip(subjects, base, strict=True) if decide(s.facts).outcome != before]
    return seen


def listed(path: Path) -> dict[str, str]:
    """The rules UNREACHABLE.md lists, each with its reason (a table row: | `RULE_ID` | reason |)."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if m := _ROW.match(line.strip()):
            out[m.group(1)] = m.group(2)
    return out


def problems(seen: dict[str, list[Subject]], unreachable: dict[str, str]) -> list[str]:
    """Why the sweep fails: a rule neither seen nor listed, a listed rule that is seen (the list is stale), a
    listed rule with no reason, or a listed id that is not a rule."""
    found = []
    for rule_id, by in seen.items():
        if not by and rule_id not in unreachable:
            found.append(f"{rule_id}: removing it changes no outcome, and UNREACHABLE.md does not list it")
        if by and rule_id in unreachable:
            found.append(f"{rule_id}: listed in UNREACHABLE.md, but removing it changes {len(by)} outcome(s); "
                         "take it off the list")
    for rule_id, reason in unreachable.items():
        if rule_id not in seen:
            found.append(f"{rule_id}: listed in UNREACHABLE.md, but it is not a rule in the table")
        elif not reason.strip():
            found.append(f"{rule_id}: listed in UNREACHABLE.md without a reason")
    return found


def how(by: list[Subject]) -> str:
    """How a rule is seen, most production-like first."""
    kinds = {s.kind for s in by}
    if "scenario" in kinds or "reachable" in kinds:
        names = [s.name for s in by if s.kind == "scenario"][:3]
        reports = sum(s.kind == "reachable" for s in by)
        return f"seen: {len(names) and ', '.join(names) or 'no plain scenario'}; {reports} reachable report(s)"
    if "declared_scenario" in kinds:
        return f"seen only through declared scenario(s): {', '.join(s.name for s in by if s.kind == 'declared_scenario')}"
    if kinds:
        return f"seen only through {len(by)} contract-only report(s) production never sends"
    return "not seen"
