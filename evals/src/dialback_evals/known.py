"""The strict known-failures list each lab job reads (spec 0048, D3; Q101, decided by Sami on Nov 9).

`evals/known-failures.json` maps each job (lab-smoke, lab-fuzz, lab-reports) to its entries. An entry is a
frozen case or scenario, the labels it fails with, its finding (an item in evals/FINDINGS.md) and the owner
of the fix. The jobs run on other owners' pull requests, so the list keeps them from being red by design
without hiding anything new:

- a listed entry must still fail with those labels: one that starts passing turns the job red, with a
  message to take it off the list, so a fix is noticed;
- anything unlisted that fails turns the job red: a frozen case, a scenario, or a label beyond its entry's;
- a new search hit is known only when every label it shows belongs to some listed entry, and the job
  counts such hits. That has a cost: a new defect that shows only an already-listed label is hidden until
  that entry is fixed. So an entry can narrow what it holds: with `"only_with": "shared_id"`, its labels make
  a hit known only when that hit's world has two documents under one id (frozen cases are judged as ever);
- an entry is added only with its finding written in FINDINGS, and removed only when the job says it now
  passes.
"""
from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

JOBS = ("lab-smoke", "lab-fuzz", "lab-reports")
ONLY_WITH = ("shared_id",)
_HEADING = re.compile(r"(?m)^### ([A-Z][0-9]+)\.")


@dataclasses.dataclass(frozen=True)
class Entry:
    case: str
    labels: tuple[str, ...]
    finding: str
    owner: str
    only_with: str | None = None      # its labels make a search hit known only on such a world


@dataclasses.dataclass(frozen=True)
class Hit:
    """A search hit: its labels, and the fact about its world an entry's condition reads."""
    labels: tuple[str, ...]
    shared_id: bool = False


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
            missing = [k for k in ("case", "labels", "finding", "owner") if not e.get(k)]
            if missing:
                raise ValueError(f"known-failures: {job} entry {e.get('case')!r} has no {', '.join(missing)}")
            if e.get("only_with") not in (None, *ONLY_WITH):
                raise ValueError(f"known-failures: {job} entry {e['case']!r} has only_with {e['only_with']!r}; "
                                 f"one of {', '.join(ONLY_WITH)}")
            parsed.append(Entry(e["case"], tuple(e["labels"]), e["finding"], e["owner"], e.get("only_with")))
        jobs[job] = tuple(parsed)
    return jobs


def for_job(path: Path | None, job: str) -> tuple[Entry, ...] | None:
    """The job's entries, or None when no list applies (a run against another folder than the repo's)."""
    return None if path is None else load(path).get(job, ())


def _covered(entries: Iterable[Entry], hit: Hit) -> set[str]:
    """The labels the list holds for this hit: every entry's, except one whose condition its world lacks."""
    return {label for e in entries if e.only_with is None or hit.shared_id for label in e.labels}


def holds(entries: Iterable[Entry], hit: Hit) -> bool:
    """Is every label this hit shows held by the list, for this hit's world?"""
    return set(hit.labels) <= _covered(entries, hit)


def judge(entries: Iterable[Entry], failing: Mapping[str, Iterable[str]],
          hits: Iterable[Hit | Iterable[str]] = ()) -> Verdict:
    """Hold the job's failures to its list. `failing` maps each failing case or scenario to the labels it
    shows now; `hits` are the new search hits, each a `Hit` or, where no entry's condition applies, just
    its labels (which claim no shared id)."""
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
    scoped = {label for e in entries if e.only_with for label in e.labels}
    unlisted: dict[tuple[str, ...], int] = {}
    for raw in hits:
        hit = raw if isinstance(raw, Hit) else Hit(tuple(raw))
        extra = tuple(sorted(set(hit.labels) - _covered(entries, hit)))
        if extra:
            unlisted[extra] = unlisted.get(extra, 0) + 1
        else:
            v.known_hits += 1
    v.problems += [f"{count} new search hit(s) show {', '.join(extra)}, which no listed entry holds"
                   + (" on a world with no shared document id" if scoped & set(extra) else "")
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
