"""Spec 0033, Task 3: freeze a violating world as a named case, load it back exactly, re-run every
frozen case against the rule, and drive the fuzz search at a pinned seed.

A frozen case is one directory holding `world.json`: the select() inputs field for field (documents,
sightings, the change request, settings, domains, ids and times, including `recorded_at`), plus what
the oracle found when it was frozen and why. It round-trips exactly, so a frozen regression always
means what it meant when it was written.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import uuid
from pathlib import Path

from dialback_ledger.select import ChangeRequest, DocumentRef, Settings, Sighting
from hypothesis import HealthCheck, given, settings
from hypothesis import seed as pin_seed

from .invariants import all_violations
from .worldgen import World, worlds

FILE = "world.json"


def _times(record: dict, *fields: str) -> dict:
    return {**record, **{f: record[f].isoformat() for f in fields}}


def _parse(record: dict, *fields: str) -> dict:
    return {**record, **{f: dt.datetime.fromisoformat(record[f]) for f in fields}}


def _dump(world: World) -> dict:
    kw, request = world.kwargs, world.kwargs["request"]
    return {
        "case_id": str(kw["case_id"]), "dataset_id": str(kw["dataset_id"]), "vendor_id": str(kw["vendor_id"]),
        "vendor_domains": list(kw["vendor_domains"]),
        "requested_cutoff": kw["requested_cutoff"].isoformat(), "case_opened_at": kw["case_opened_at"].isoformat(),
        "request": None if request is None else _times(dataclasses.asdict(request), "received_at"),
        "settings": dataclasses.asdict(world.settings),
        "poison": sorted(world.poison),
        "documents": [_times(dataclasses.asdict(d), "received_at", "recorded_at") for d in kw["documents"]],
        "sightings": [_times(dataclasses.asdict(s), "received_at", "recorded_at") for s in kw["sightings"]],
    }


def _load(data: dict) -> World:
    chosen = Settings(**data["settings"])
    request = data["request"]
    kwargs = {
        "case_id": uuid.UUID(data["case_id"]), "dataset_id": uuid.UUID(data["dataset_id"]),
        "vendor_id": uuid.UUID(data["vendor_id"]), "vendor_domains": tuple(data["vendor_domains"]),
        "request": None if request is None else ChangeRequest(**_parse(request, "received_at")),
        "requested_cutoff": dt.datetime.fromisoformat(data["requested_cutoff"]),
        "case_opened_at": dt.datetime.fromisoformat(data["case_opened_at"]),
        "documents": [DocumentRef(**_parse(d, "received_at", "recorded_at")) for d in data["documents"]],
        "sightings": [Sighting(**_parse(s, "received_at", "recorded_at")) for s in data["sightings"]],
        "settings": chosen,
    }
    return World(kwargs=kwargs, poison=frozenset(data["poison"]), settings=chosen)


def freeze(world: World, label: str, out_dir: Path, *, labels: list[str] | tuple[str, ...] = (), why: str = "") -> Path:
    """Write `world` as `<out_dir>/<label>/world.json` and return the case directory."""
    case = Path(out_dir) / label
    case.mkdir(parents=True, exist_ok=True)
    body = {"label": label, "labels": list(labels), "why": why, **_dump(world)}
    (case / FILE).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return case


def load_world(case_dir: Path) -> World:
    return _load(json.loads((Path(case_dir) / FILE).read_text(encoding="utf-8")))


def load_all(out_dir: Path) -> list[tuple[str, World]]:
    root = Path(out_dir)
    if not root.is_dir():
        return []
    return [(case.name, load_world(case)) for case in sorted(root.iterdir()) if (case / FILE).is_file()]


def rerun(out_dir: Path) -> list[tuple[str, list[str]]]:
    """Every frozen case the rule still fails, with what the oracle finds now (a found case stays found)."""
    return [(label, found) for label, world in load_all(out_dir) if (found := all_violations(world))]


class _Found(Exception):
    def __init__(self, world: World, labels: list[str]) -> None:
        super().__init__(labels)
        self.world, self.labels = world, labels


def fuzz(strategy=None, *, examples: int, seed: int, out_dir: Path) -> list[str]:
    """Search `examples` worlds at a pinned `seed`. On the first violation, Hypothesis shrinks it to a
    minimal world, which is frozen under `out_dir`; its labels are returned. Returns [] when clean."""
    strategy = worlds() if strategy is None else strategy

    @pin_seed(seed)
    @settings(max_examples=examples, deadline=None, database=None, report_multiple_bugs=False,
              suppress_health_check=[HealthCheck.too_slow])
    @given(strategy)
    def search(world: World) -> None:
        labels = all_violations(world)
        if labels:
            raise _Found(world, labels)

    try:
        search()
    except _Found as hit:
        label = f"fuzz-seed{seed}-{hit.labels[0].lower().replace('_', '-')}"
        freeze(hit.world, label, out_dir, labels=hit.labels,
               why=f"found by lab fuzz at seed {seed} within {examples} examples, shrunk by Hypothesis")
        return hit.labels
    return []
