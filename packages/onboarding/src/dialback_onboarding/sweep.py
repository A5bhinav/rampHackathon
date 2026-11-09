"""Spec 0041: the sweep. Reads new vendors and their contacts, decides each one's anchor, and writes one
JSON report that nothing else reads (shadow mode). It writes nothing to Ramp or any database.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dialback_onboarding.anchor import RULE_VERSION, Settings, choose_anchor, normalize_name
from dialback_onboarding.ramp import API, RampError

FICTIONAL = re.compile(r"^\+1\d{3}55501\d{2}$")    # NANP 555-0100 to 555-0199, reserved for fiction
ANY_NUMBER = re.compile(r"\+\d{8,15}")
MAX_PAGES = 1000


class RefusedNumber(Exception):
    """The report would hold a number outside 555-0100 to 555-0199."""


def load_sources(path: Path) -> dict[str, dict[str, Any]]:
    """The stand-in sources (spec 0041): `{"vendors": {<vendor name>: {"tax_id", "registry", "website"}}}`,
    keyed here by the normalized name. Raises ValueError on anything unreadable or ambiguous."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"can't read the sources file {path}: {exc}") from None
    try:
        entries = list(data["vendors"].items())
    except (TypeError, KeyError, AttributeError):
        raise ValueError(f"{path}: needs a `vendors` object keyed by vendor name") from None
    out: dict[str, dict[str, Any]] = {}
    for name, entry in entries:
        key = normalize_name(name)
        if key is None or not isinstance(entry, dict):
            raise ValueError(f"{path}: unreadable entry {name!r}")
        if key in out:
            raise ValueError(f"{path}: two entries name the same vendor ({name!r})")
        out[key] = entry
    return out


def parse_time(raw: object) -> dt.datetime:
    """An ISO 8601 time; one without a zone is UTC. Raises ValueError."""
    try:
        when = dt.datetime.fromisoformat(raw)   # a TypeError for anything but a string
    except TypeError:
        raise ValueError(f"not a time: {raw!r}") from None
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def _list(page: Mapping[str, Any], what: str) -> list[Any]:
    data = page.get("data")
    if not isinstance(data, list):
        raise RampError(f"unreadable {what}: `data` is not a list")
    return data


def _new_vendors(get: Callable[[str], dict], since: dt.datetime) -> list[tuple[dt.datetime, dict]]:
    target: str | None = f"{API}/vendors?page_size=100"
    seen: set[str] = set()
    kept = []
    while target is not None:
        if target in seen or len(seen) >= MAX_PAGES:
            raise RampError("the vendor list does not end")
        seen.add(target)
        page = get(target)
        for vendor in _list(page, "vendor list"):
            if not isinstance(vendor, dict) or not isinstance(vendor.get("id"), str):
                raise RampError("unreadable vendor in the list")
            try:
                created = parse_time(vendor.get("created_at"))
            except ValueError:
                raise RampError(f"vendor {vendor['id']}: unreadable created_at") from None
            if created >= since:
                kept.append((created, vendor))
        nxt = page.get("page", {}).get("next") if isinstance(page.get("page"), dict) else None
        target = nxt if isinstance(nxt, str) and nxt else None
    return sorted(kept, key=lambda pair: (pair[0], pair[1]["id"]))


def sweep(get: Callable[[str], dict], *, dataset_id: uuid.UUID, since: dt.datetime,
          sources: Mapping[str, Mapping[str, Any]], settings: Settings) -> dict[str, Any]:
    """Build the report. Raises RampError if any read fails: no partial report."""
    results = []
    for created, vendor in _new_vendors(get, since):
        contacts = _list(get(f"{API}/vendors/{quote(vendor['id'], safe='')}/contacts"), "contact list")
        entry = sources.get(normalize_name(vendor.get("name")) or "", {})
        claimed = {"id": vendor["id"], "name": vendor.get("name"), "state": vendor.get("state"),
                   "tax_id": entry.get("tax_id")}   # the mock has no tax ID field, so the fixture stands in
        result = choose_anchor(claimed, contacts, {"registry": entry.get("registry"),
                                                   "website": entry.get("website")}, settings)
        results.append({"name": vendor.get("name"), "created_at": created.isoformat(), **result})
    anchored = sum(r["outcome"] == "anchored" for r in results)
    return {
        "report": "onboarding-anchors (shadow mode: nothing reads this)",
        "rule_version": RULE_VERSION,
        "dataset_id": str(dataset_id),
        "since": since.isoformat(),
        "as_of": settings.as_of.isoformat(),
        "settings": {"min_domain_age_days": settings.min_domain_age_days,
                     "min_independent_kinds": settings.min_independent_kinds},
        "counts": {"vendors": len(results), "anchored": anchored, "no_anchor": len(results) - anchored},
        "vendors": results,
    }


def write_report(report: Mapping[str, Any], path: Path) -> None:
    """Write the report whole or not at all, and never with a number outside 555-0100 to 555-0199."""
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    refused = {n for n in ANY_NUMBER.findall(text) if not FICTIONAL.match(n)}
    if refused:
        raise RefusedNumber(f"refusing to write {len(refused)} number(s) outside 555-0100 to 555-0199")
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
