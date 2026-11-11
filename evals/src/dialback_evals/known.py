"""The strict known-failures list each lab job reads (spec 0048, D3; Q101, decided by Sami on Nov 9).

`evals/known-failures.json` maps each job (lab-smoke, lab-fuzz, lab-reports, lab-pdf) to its entries. An entry is a
frozen case or scenario, the labels it fails with, its finding (an item in evals/FINDINGS.md) and the owner
of the fix. The jobs run on other owners' pull requests, so the list keeps them from being red by design
without hiding anything new:

- a listed entry must still fail with those labels: one that starts passing turns the job red, with a
  message to take it off the list, so a fix is noticed;
- anything unlisted that fails turns the job red: a frozen case, a scenario, or a label beyond its entry's;
- a new search hit is known only when every label it shows belongs to some listed entry, and the job
  counts such hits. That has a cost: a new defect that shows only an already-listed label is hidden until
  that entry is fixed;
- an entry is added only with its finding written in FINDINGS, and removed only when the job says it now
  passes.

Spec 0052, decision 6 (Q120): `lab-docs` keys its entries by pattern, not by case: the check, the phrasing
and the placement, each naming its finding. Such an entry is held exactly like a case, under the case name
`<check>/<phrasing>/<placement>` with the check as its one label: a listed pattern must still fail, and a
failing pattern the list lacks turns the job red.
"""
from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

JOBS = ("lab-smoke", "lab-fuzz", "lab-reports", "lab-docs", "lab-pdf")   # lab-pdf: evals/tests/test_pdf_tricks.py (spec 0092)
PATTERN_JOBS = frozenset({"lab-docs"})                  # entries keyed by pattern (spec 0052, decision 6)
PATTERN_FIELDS = ("check", "phrasing", "placement")
_HEADING = re.compile(r"(?m)^### ([A-Z][0-9]+)\.")


@dataclasses.dataclass(frozen=True)
class Entry:
    case: str
    labels: tuple[str, ...]
    finding: str
    owner: str


@dataclasses.dataclass
class Verdict:
    problems: list[str] = dataclasses.field(default_factory=list)
    known: list[str] = dataclasses.field(default_factory=list)    # listed cases still failing as listed
    known_hits: int = 0                                            # search hits whose labels are all listed

    @property
    def red(self) -> bool:
        return bool(self.problems)


def load(path: Path) -> dict[str, tuple[Entry, ...]]:
    """The list, per job. Refuses an unknown job and an entry missing any of its four fields."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    jobs = {}
    for job, entries in raw.items():
        if job.startswith("_"):                                    # notes, never entries
            continue
        if job not in JOBS:
            raise ValueError(f"known-failures: unknown job {job!r}; one of {', '.join(JOBS)}")
        parsed = []
        for e in entries:
            if job in PATTERN_JOBS:
                missing = [k for k in (*PATTERN_FIELDS, "finding", "owner") if not e.get(k)]
                if missing:
                    raise ValueError(f"known-failures: {job} entry {e!r} has no {', '.join(missing)}")
                parsed.append(Entry(pattern(*(e[k] for k in PATTERN_FIELDS)), (e["check"],), e["finding"], e["owner"]))
                continue
            missing = [k for k in ("case", "labels", "finding", "owner") if not e.get(k)]
            if missing:
                raise ValueError(f"known-failures: {job} entry {e.get('case')!r} has no {', '.join(missing)}")
            parsed.append(Entry(e["case"], tuple(e["labels"]), e["finding"], e["owner"]))
        jobs[job] = tuple(parsed)
    return jobs


def pattern(check: str, phrasing: str, placement: str) -> str:
    """A pattern's case name, as `lab docs` reports it (docgen.pattern_key)."""
    return f"{check}/{phrasing}/{placement}"


def for_job(path: Path | None, job: str) -> tuple[Entry, ...] | None:
    """The job's entries, or None when no list applies (a run against another folder than the repo's)."""
    return None if path is None else load(path).get(job, ())


def judge(entries: Iterable[Entry], failing: Mapping[str, Iterable[str]], hits: Iterable[Iterable[str]] = ()) -> Verdict:
    """Hold the job's failures to its list. `failing` maps each failing case or scenario to the labels it
    shows now; `hits` are the label sets of new search hits."""
    entries = tuple(entries)
    v = Verdict()
    now = {case: set(labels) for case, labels in failing.items()}
    listed: dict[str, set[str]] = {}
    for e in entries:
        listed.setdefault(e.case, set()).update(e.labels)
        stopped = sorted(set(e.labels) - now.get(e.case, set()))
        if stopped:
            v.problems.append(f"{e.case} is on the known-failures list ({e.finding}, {e.owner}) but no longer fails "
                              f"with {', '.join(stopped)}: take it off the list (or correct its labels)")
        else:
            v.known.append(e.case)
    for case, labels in sorted(now.items()):
        extra = sorted(labels - listed.get(case, set()))
        if extra:
            v.problems.append(f"{case} fails with {', '.join(extra)}, which the known-failures list does not hold")
    every = set().union(*listed.values()) if listed else set()
    unlisted: dict[tuple[str, ...], int] = {}
    for hit in hits:
        extra = tuple(sorted(set(hit) - every))
        if extra:
            unlisted[extra] = unlisted.get(extra, 0) + 1
        else:
            v.known_hits += 1
    v.problems += [f"{count} new search hit(s) show {', '.join(extra)}, which no listed entry holds"
                   for extra, count in sorted(unlisted.items())]
    return v


def missing_findings(entries: Iterable[Entry], findings_text: str) -> list[str]:
    """Entries whose finding is not an item in FINDINGS (an entry is added only with its finding written)."""
    written = set(_HEADING.findall(findings_text))
    return [f"{e.case}: its finding {e.finding} is not written in evals/FINDINGS.md" for e in entries
            if e.finding not in written]


def report(job: str, v: Verdict, entries: tuple[Entry, ...], findings: list[str]) -> list[str]:
    """The lines a job prints about its list."""
    lines = [f"{job}: {len(v.known)} of {len(entries)} known failure(s) still fail as listed"]
    if v.known_hits:
        lines.append(f"{job}: {v.known_hits} search hit(s) showed only listed labels (counted as known; a new defect "
                     "with only those labels stays hidden until its entry is fixed)")
    lines += [f"{job}: {p}" for p in v.problems + findings]
    return lines
